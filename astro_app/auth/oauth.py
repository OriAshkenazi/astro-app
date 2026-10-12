"""Sign in with ChatGPT, open-source client flow (OIDC + PKCE, dynamic registration, loopback callback).

Steps:
  start()     -> PendingAuth + authorization URL (fresh state, nonce, PKCE per attempt)
  complete()  -> validates the callback, exchanges the code, verifies the ID token and the
                 granted scopes, then saves the credential record
  refresh()   -> rotating refresh-token grant, serialised per registration
  revoke()    -> ends the renewable session on sign-out
"""
from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
import jwt

from . import config as C
from .store import Credential, CredentialStore


class AuthError(Exception):
    """Sign-in failed; `user_message` is safe to show, never contains credentials."""

    user_message = "Sign-in could not be completed. Please try again."


class AuthDenied(AuthError):
    user_message = "Sign-in was cancelled in the browser."


class AuthStateError(AuthError):
    user_message = "Sign-in could not be verified (expired or mismatched attempt). Please start again."


class RegistrationIncomplete(AuthError):
    user_message = "ChatGPT did not finish registering this app. Please start again."


class ClientMismatch(AuthError):
    user_message = "ChatGPT returned a different app registration than expected. Please start again."


class IdentityMismatch(AuthError):
    user_message = "You signed in with a different ChatGPT account than the one selected."


class InvalidGrant(AuthError):
    """Code exchange or refresh token is unusable; start a fresh authorization."""
    user_message = "Your ChatGPT session has ended. Please sign in again."


class InvalidClient(AuthError):
    user_message = "This app's ChatGPT registration is no longer valid. Please sign in again."


class TokenEndpointError(AuthError):
    def __init__(self, status: int, code: str | None, request_id: str | None, description: str | None = None):
        self.status, self.code, self.request_id, self.description = status, code, request_id, description
        super().__init__(f"token endpoint HTTP {status} error={code} description={description!r} "
                         f"request_id={request_id}")


class CodeExchangeRejected(AuthError):
    """The token endpoint refused the authorization code. Recovery (per the SIWC docs): start a
    fresh authorization with the issued client ID, which `complete()` has already saved."""
    user_message = "ChatGPT rejected the sign-in code. Please run the sign-in again."

    def __init__(self, message: str, client_id: str):
        self.client_id = client_id
        super().__init__(message)


REFRESH_UNUSABLE = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                    "refresh_token_invalidated", "refresh_token_reused"}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@dataclass
class PendingAuth:
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    client_id: str                     # DYNAMIC_CLIENT for a new registration, else the issued id
    host_id: str
    expected_subject: str | None = None
    created_at: float = field(default_factory=time.time)

    @property
    def is_registration(self) -> bool:
        return self.client_id == C.DYNAMIC_CLIENT

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.created_at > C.PENDING_TTL_S


def start(redirect_uri: str, host_id: str, account: Credential | None = None,
          force_consent: bool = False) -> tuple[PendingAuth, str]:
    verifier = _b64url(secrets.token_bytes(32))  # 43 chars, as in the reference client
    pending = PendingAuth(state=_b64url(secrets.token_bytes(24)), nonce=_b64url(secrets.token_bytes(24)),
                          code_verifier=verifier, redirect_uri=redirect_uri,
                          client_id=account.client_id if account else C.DYNAMIC_CLIENT, host_id=host_id,
                          expected_subject=(account.subject or None) if account else None)
    params = {"client_id": pending.client_id, "response_type": "code", "redirect_uri": redirect_uri,
              "scope": C.SCOPE, "resource": C.RESOURCE, "state": pending.state, "nonce": pending.nonce,
              "code_challenge_method": "S256",
              "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
              "ext_agent_host_id": host_id}
    if account is None:
        params["agent_name_hint"] = C.AGENT_NAME
    elif account.email:
        # login_hint only: browser URLs end up in process arguments, so no ID token goes in them
        params["login_hint"] = account.email
    if force_consent:
        params["prompt"] = "consent"
    return pending, f"{C.AUTHORIZE_URL}?{urlencode(params)}"


class IdTokenVerifier:
    """Verifies OpenAI ID tokens against the published JWKS (cached; refetched on an unknown kid)."""

    def __init__(self, http: httpx.AsyncClient, jwks_url: str = C.JWKS_URL, issuer: str = C.ISSUER):
        self.http, self.jwks_url, self.issuer = http, jwks_url, issuer
        self._keys: dict[str, jwt.PyJWK] = {}

    async def _load(self) -> None:
        r = await self.http.get(self.jwks_url)
        r.raise_for_status()
        self._keys = {k["kid"]: jwt.PyJWK(k) for k in r.json()["keys"] if "kid" in k}

    async def verify(self, token: str, audience: str, nonce: str) -> dict:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as e:
            raise AuthError(f"malformed ID token: {e}") from e
        if kid not in self._keys:
            await self._load()
        if kid not in self._keys:
            raise AuthError("ID token signed with an unknown key")
        key = self._keys[kid]
        try:
            claims = jwt.decode(token, key=key.key, algorithms=[key.algorithm_name or "RS256"], audience=audience,
                                issuer=self.issuer, leeway=C.CLOCK_SKEW_S,
                                options={"require": ["sub", "exp", "iat", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise AuthError(f"ID token rejected: {e}") from e
        if claims.get("azp") not in (None, audience):
            raise AuthError("ID token authorised party mismatch")
        if claims.get("nonce") != nonce:
            raise AuthError("ID token nonce mismatch")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise AuthError("ID token has no subject")
        return claims


def _error_info(r: httpx.Response) -> tuple[str | None, str | None]:
    """(code, description) from {"error": "x", "error_description": ...}, {"error": {"code", "message"}},
    {"code": ...} or {"detail": ...}. Descriptions are diagnostic text and never contain credentials."""
    try:
        body = r.json()
    except ValueError:
        return None, r.text[:200] or None
    if not isinstance(body, dict):
        return None, None
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("code"), err.get("message")
    desc = body.get("error_description") or body.get("message") or body.get("detail")
    return (err if isinstance(err, str) else body.get("code")), (str(desc)[:300] if desc else None)


async def _token_request(http: httpx.AsyncClient, data: dict) -> dict:
    r = await http.post(C.TOKEN_URL, data=data, headers={"accept": "application/json"})
    if r.status_code == 200:
        return r.json()
    code, desc = _error_info(r)
    rid = r.headers.get("openai-request-id") or r.headers.get("x-request-id")
    detail = f"HTTP {r.status_code} error={code} description={desc!r} request_id={rid}"
    if code in REFRESH_UNUSABLE:
        raise InvalidGrant(detail)
    if code == "invalid_client":
        raise InvalidClient(detail)
    raise TokenEndpointError(r.status_code, code, rid, desc)


async def complete(pending: PendingAuth | None, params: dict, http: httpx.AsyncClient, store: CredentialStore,
                   verifier: IdTokenVerifier, now: float | None = None) -> Credential:
    """Handle the loopback callback query `params` for `pending`; returns the saved credential."""
    if pending is None or pending.expired(now) or not secrets.compare_digest(str(params.get("state", "")), pending.state):
        raise AuthStateError("state missing, expired or mismatched")
    if params.get("error"):
        if params["error"] == "access_denied":
            raise AuthDenied("user declined")
        raise AuthError(f"authorization error: {params['error']}")
    code = params.get("code")
    if not code:
        raise AuthError("callback without authorization code")
    returned = params.get("client_id")
    if pending.is_registration:
        if not returned or returned == C.DYNAMIC_CLIENT:
            raise RegistrationIncomplete("new registration callback without an issued client_id")
        client_id = returned
    else:
        if returned and returned != pending.client_id:
            raise ClientMismatch("callback client_id differs from the selected registration")
        client_id = pending.client_id

    if pending.is_registration and store.load(client_id) is None:
        # keep the issued registration before the one-time code is spent, so a retry reuses it
        store.save(Credential(client_id=client_id, subject="", ext_agent_host_id=pending.host_id))
    try:
        tok = await _token_request(http, {"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                          "code_verifier": pending.code_verifier,
                                          "redirect_uri": pending.redirect_uri, "resource": C.RESOURCE})
    except (InvalidGrant, InvalidClient, TokenEndpointError) as e:
        raise CodeExchangeRejected(f"code exchange for client {client_id[:10]}…: {e}", client_id) from e
    if not isinstance(tok.get("id_token"), str) or not tok.get("access_token"):
        raise AuthError("token response missing id_token or access_token")
    claims = await verifier.verify(tok["id_token"], audience=client_id, nonce=pending.nonce)
    if pending.expected_subject and claims["sub"] != pending.expected_subject:
        raise IdentityMismatch("ID token subject differs from the selected account")

    existing = store.load(client_id)
    base = existing or Credential(client_id=client_id, subject=claims["sub"], ext_agent_host_id=pending.host_id)
    base.subject = claims["sub"]
    if existing and existing.subject and existing.subject != claims["sub"]:
        raise IdentityMismatch("stored registration belongs to another account")
    base.email = claims.get("email") or base.email
    base.name = claims.get("name") or base.name
    base.ext_agent_host_id = pending.host_id
    cred = base.with_tokens(tok, now)
    store.save(cred)
    store.set_active(client_id)
    return cred


_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def _refresh_lock(store: CredentialStore, client_id: str):
    """Serialise refreshes of one registration across tasks (asyncio lock) and processes (flock)."""
    lock = _locks.setdefault(client_id, asyncio.Lock())
    async with lock:
        with open(store.lock_path(client_id), "a+") as fh:
            await asyncio.to_thread(fcntl.flock, fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


async def refresh(cred: Credential, http: httpx.AsyncClient, store: CredentialStore,
                  now: float | None = None) -> Credential:
    async with _refresh_lock(store, cred.client_id):
        latest = store.load(cred.client_id) or cred
        if latest.refresh_token != cred.refresh_token and latest.signed_in:
            return latest  # another task or process already rotated it
        if not latest.refresh_token:
            raise InvalidGrant("no refresh token stored")
        try:
            tok = await _token_request(http, {"grant_type": "refresh_token", "client_id": latest.client_id,
                                              "refresh_token": latest.refresh_token, "resource": C.RESOURCE})
        except InvalidGrant:
            store.save(latest.signed_out())  # unusable: clear tokens, keep the registration mapping
            raise
        new = latest.with_tokens(tok, now)
        store.save(new)
        return new


async def revoke(cred: Credential, http: httpx.AsyncClient, store: CredentialStore, attempts: int = 3,
                 sleep=asyncio.sleep) -> bool:
    """Sign out: revoke the refresh token, then clear tokens locally either way.

    Returns True when remote revocation was confirmed (an empty HTTP 200)."""
    confirmed = False
    if cred.refresh_token:
        endpoint = None
        for i in range(attempts):
            try:
                if endpoint is None:
                    d = await http.get(C.DISCOVERY_URL)
                    d.raise_for_status()
                    endpoint = d.json()["revocation_endpoint"]
                r = await http.post(endpoint, data={"token": cred.refresh_token, "token_type_hint": "refresh_token",
                                                    "client_id": cred.client_id})
                if r.status_code == 200:
                    confirmed = True
                    break
                if r.status_code < 500:
                    break
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            if i < attempts - 1:
                await sleep(2 ** i)
    store.save(cred.signed_out())
    return confirmed
