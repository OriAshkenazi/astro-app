"""In-memory stand-in for OpenAI's auth server and Responses API, served through httpx.MockTransport.

It signs real RS256 ID tokens, publishes a JWKS, checks PKCE, rotates refresh tokens and can
replay scripted Responses streams, so tests exercise the same validation as production.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from astro_app.auth import config as C

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key"


def _jwk() -> dict:
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key()))
    return {**d, "kid": KID, "alg": "RS256", "use": "sig"}


@dataclass
class FakeOpenAI:
    issued_client_id: str = "oaiapp_test123456"
    subject: str = "user-sub-1"
    email: str = "person@example.com"
    granted_scope: str = C.SCOPE
    id_token_overrides: dict = field(default_factory=dict)
    sign_with_other_key: bool = False
    revoke_status: list[int] = field(default_factory=lambda: [200])
    responses: list = field(default_factory=list)       # scripted Responses replies (see sse())
    models: list[dict] = field(default_factory=lambda: [
        {"slug": "gpt-test-large", "display_name": "GPT Test Large", "visibility": "list"},
        {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide"}])
    # recorded state
    codes: dict = field(default_factory=dict)
    refresh_tokens: dict = field(default_factory=dict)  # token -> valid?
    access_tokens: set = field(default_factory=set)
    calls: list = field(default_factory=list)
    response_bodies: list = field(default_factory=list)
    _n: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())

    # ------------------------------------------------------------- browser side
    def authorize(self, url: str, approve: bool = True) -> dict:
        """Simulate the browser visit to the authorize URL; returns the callback query params."""
        q = dict(parse_qsl(urlsplit(url).query))
        if not approve:
            return {"error": "access_denied", "state": q["state"]}
        self._n += 1
        code = f"code-{self._n}"
        self.codes[code] = q
        out = {"code": code, "state": q["state"], "scope": self.granted_scope}
        if q["client_id"] == C.DYNAMIC_CLIENT:
            out["client_id"] = self.issued_client_id
        return out

    def id_token(self, aud: str, nonce: str, **over) -> str:
        now = int(time.time())
        claims = {"iss": C.ISSUER, "aud": aud, "sub": self.subject, "email": self.email, "nonce": nonce,
                  "iat": now, "exp": now + 3600, **self.id_token_overrides, **over}
        key = _OTHER_KEY if self.sign_with_other_key else _KEY
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})

    # ------------------------------------------------------------- server side
    def _tokens(self, client_id: str, nonce: str | None) -> dict:
        self._n += 1
        at, rt = f"at-{self._n}", f"rt-{self._n}"
        self.access_tokens.add(at)
        self.refresh_tokens[rt] = True
        out = {"access_token": at, "refresh_token": rt, "token_type": "Bearer", "expires_in": 3600,
               "scope": self.granted_scope}
        if nonce is not None:
            out["id_token"] = self.id_token(client_id, nonce)
        return out

    def _handle(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url).split("?")[0]
        self.calls.append((req.method, url))
        if url == C.JWKS_URL:
            return httpx.Response(200, json={"keys": [_jwk()]})
        if url == C.DISCOVERY_URL:
            return httpx.Response(200, json={"issuer": C.ISSUER, "revocation_endpoint": C.ISSUER + "/oauth/revoke",
                                             "jwks_uri": C.JWKS_URL, "token_endpoint": C.TOKEN_URL})
        if url == C.ISSUER + "/oauth/revoke":
            status = self.revoke_status.pop(0) if len(self.revoke_status) > 1 else self.revoke_status[0]
            if status == 200:
                self.refresh_tokens[dict(parse_qsl(req.content.decode()))["token"]] = False
            return httpx.Response(status)
        if url == C.TOKEN_URL:
            f = dict(parse_qsl(req.content.decode()))
            if f["grant_type"] == "authorization_code":
                q = self.codes.pop(f["code"], None)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                ok = (q and q["code_challenge"] == challenge and q["redirect_uri"] == f["redirect_uri"]
                      and f["resource"] == C.RESOURCE and f["client_id"] != C.DYNAMIC_CLIENT)
                if not ok:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._tokens(f["client_id"], q["nonce"]))
            if f["grant_type"] == "refresh_token":
                rt = f["refresh_token"]
                if rt not in self.refresh_tokens:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                if not self.refresh_tokens[rt]:
                    return httpx.Response(400, json={"error": "refresh_token_reused"})
                self.refresh_tokens[rt] = False
                return httpx.Response(200, json=self._tokens(f["client_id"], None))
        if url == C.RESOURCE + "/models":
            if req.headers.get("authorization", "").removeprefix("Bearer ") not in self.access_tokens:
                return httpx.Response(401, json={"detail": "unauthorized"})
            return httpx.Response(200, json={"models": self.models})
        if url == C.RESOURCE + "/responses":
            body = json.loads(req.content)
            self.response_bodies.append(body)
            if req.headers.get("authorization", "").removeprefix("Bearer ") not in self.access_tokens:
                return httpx.Response(401, json={"detail": "Signed identity not accepted"})
            reply = self.responses.pop(0) if self.responses else sse_text("ok")
            return reply if isinstance(reply, httpx.Response) else httpx.Response(
                200, headers={"content-type": "text/event-stream", "x-request-id": "req_test"}, content=reply)
        return httpx.Response(404, json={"detail": f"no route {url}"})


def sse(*events: dict) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def sse_text(text: str, chunks: int = 3, model: str = "gpt-test-large") -> bytes:
    step = max(1, len(text) // chunks)
    parts = [text[i:i + step] for i in range(0, len(text), step)]
    done = {"type": "response.completed", "response": {
        "id": "resp_1", "model": model, "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 10, "output_tokens": len(text)}}}
    return sse({"type": "response.created", "response": {"id": "resp_1"}},
               *({"type": "response.output_text.delta", "delta": p} for p in parts), done)


def sse_failed(code: str, message: str = "failed", partial: str = "partial ") -> bytes:
    return sse({"type": "response.created", "response": {"id": "resp_2"}},
               {"type": "response.output_text.delta", "delta": partial},
               {"type": "response.failed", "response": {"id": "resp_2", "status": "failed",
                                                        "error": {"code": code, "message": message}}})
