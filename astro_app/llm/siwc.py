"""Responses API client running on the user's ChatGPT plan (Sign in with ChatGPT, open-source flow).

Preview rules (developers.openai.com/siwc/token-sharing-open-source/preview-limitations):
store=false and stream=true on every request; instructions instead of system-role items; no
temperature, max_output_tokens, metadata, previous_response_id, etc. Success means a
`response.completed` event; usage-limit errors can arrive mid-stream as `response.failed`.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from ..auth.config import RESOURCE
from ..auth.session import AuthSession
from .base import (AccessForbidden, AuthRejected, IncompleteResponse, InvalidOutput, LLMError, LLMRequest,
                   LLMResult, Model, PlanNotEligible, TemporarilyUnavailable, UnsupportedRequest,
                   UsageLimitExceeded)

RESPONSES_URL = f"{RESOURCE}/responses"
MODELS_URL = f"{RESOURCE}/models"
FORBIDDEN_FIELDS = {"background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation",
                    "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier", "temperature",
                    "top_logprobs", "top_p", "truncation", "user", "previous_response_id"}
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

_BY_CODE = {
    "subscription_sharing_usage_limit_exceeded": UsageLimitExceeded,
    "subscription_sharing_user_not_eligible": PlanNotEligible,
    "subscription_sharing_usage_unavailable": TemporarilyUnavailable,
    "subscription_sharing_user_unavailable": TemporarilyUnavailable,
    "subscription_sharing_unsupported_capability": UnsupportedRequest,
    "subscription_sharing_route_not_supported": AccessForbidden,
    "subscription_sharing_invalid_user": AuthRejected,
    "chatpass_v2_scope_not_authorized": AccessForbidden,
    "chatpass_v2_invalid_authorization_context": AccessForbidden,
}


def error_for(status: int | None, code: str | None, message: str, request_id: str | None,
              param: str | None = None) -> LLMError:
    kw = dict(status=status, code=code, request_id=request_id, param=param)
    if code in _BY_CODE:
        return _BY_CODE[code](message, **kw)
    if status == 401:
        return AuthRejected(message, **kw)
    if status == 403:
        return AccessForbidden(message, **kw)
    if status in (429, 500, 502, 503, 504):
        return TemporarilyUnavailable(message, **kw)
    if status == 400:
        return UnsupportedRequest(message, **kw)
    return LLMError(message, **kw)


def _http_error(r: httpx.Response) -> LLMError:
    rid = r.headers.get("x-request-id") or r.headers.get("openai-request-id")
    code = param = None
    message = r.text[:500]
    try:
        body = r.json()
        if isinstance(body.get("error"), dict):
            code, param = body["error"].get("code"), body["error"].get("param")
            message = body["error"].get("message") or message
        elif "detail" in body:  # direct-route admission: diagnostic text, not a stable code
            message = str(body["detail"])
    except (ValueError, AttributeError):
        pass
    return error_for(r.status_code, code, message, rid, param)


def _output_text(response: dict) -> str | None:
    parts = [c.get("text", "") for item in response.get("output", []) if item.get("type") == "message"
             for c in item.get("content", []) if c.get("type") == "output_text"]
    return "".join(parts) if parts else None


def build_body(req: LLMRequest, model: str) -> dict:
    body = {"model": model, "instructions": req.instructions,
            "input": [{"role": "user", "content": req.input}] if isinstance(req.input, str) else req.input,
            "store": False, "stream": True}
    if req.schema is not None:
        body["text"] = {"format": {"type": "json_schema", "name": req.schema_name, "schema": req.schema,
                                   "strict": True}}
    bad = FORBIDDEN_FIELDS & body.keys()
    if bad or any(isinstance(i, dict) and i.get("role") == "system" for i in body["input"]):
        raise UnsupportedRequest(f"request uses fields unsupported by ChatGPT plan usage: {sorted(bad) or 'system'}")
    return body


class SIWCClient:
    def __init__(self, session: AuthSession, http: httpx.AsyncClient, model: str | None = None,
                 max_attempts: int = 3, sleep=asyncio.sleep):
        self.session, self.http, self.model = session, http, model
        self.max_attempts, self.sleep = max_attempts, sleep

    async def _authorized(self, call):
        """Run `call(token)`; on a 401, retry once with a refreshed token."""
        token = await self.session.access_token()
        try:
            return await call(token)
        except AuthRejected:
            fresh = await self.session.access_token(rejected=token)
            return await call(fresh)

    async def list_models(self) -> list[Model]:
        async def call(token):
            r = await self.http.get(MODELS_URL, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
            if r.status_code != 200:
                raise _http_error(r)
            return [Model(m["slug"], m.get("display_name") or m["slug"]) for m in r.json().get("models", [])
                    if m.get("visibility") == "list"]
        return await self._authorized(call)

    async def _model(self) -> str:
        if not self.model:
            models = await self.list_models()
            if not models:
                raise PlanNotEligible("no models available to this account")
            self.model = models[0].slug
        return self.model

    async def complete(self, req: LLMRequest) -> LLMResult:
        body = build_body(req, await self._model())
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = await self._authorized(lambda token: self._stream(token, body))
                if req.schema is not None:
                    try:
                        result.data = json.loads(result.text)
                    except ValueError as e:
                        raise InvalidOutput(f"output is not valid JSON: {e}", request_id=result.request_id) from e
                return result
            except LLMError as e:
                if not e.retryable or attempt == self.max_attempts:
                    raise
                await self.sleep(min(2 ** attempt, 30))
        raise AssertionError("unreachable")

    async def _stream(self, token: str, body: dict) -> LLMResult:
        headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
        try:
            async with self.http.stream("POST", RESPONSES_URL, json=body, headers=headers, timeout=TIMEOUT) as r:
                rid = r.headers.get("x-request-id") or r.headers.get("openai-request-id")
                if r.status_code != 200:
                    await r.aread()
                    raise _http_error(r)
                deltas: list[str] = []
                data_lines: list[str] = []
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        continue
                    if line.strip() or not data_lines:
                        continue
                    raw, data_lines = "\n".join(data_lines), []
                    if raw == "[DONE]":
                        continue
                    ev = json.loads(raw)
                    t = ev.get("type")
                    if t == "response.output_text.delta":
                        deltas.append(ev.get("delta", ""))
                    elif t == "response.completed":
                        resp = ev.get("response", {})
                        text = _output_text(resp)
                        return LLMResult(text=text if text is not None else "".join(deltas), model=resp.get("model"),
                                         request_id=rid, usage=resp.get("usage") or {})
                    elif t == "response.failed":
                        err = (ev.get("response") or {}).get("error") or {}
                        raise error_for(None, err.get("code"), err.get("message", "response failed"), rid,
                                        err.get("param"))
                    elif t == "response.incomplete":
                        reason = ((ev.get("response") or {}).get("incomplete_details") or {}).get("reason")
                        raise IncompleteResponse(f"response incomplete: {reason}", request_id=rid)
                    elif t == "error":
                        err = ev.get("error") if isinstance(ev.get("error"), dict) else ev
                        raise error_for(None, err.get("code"), err.get("message", "stream error"), rid,
                                        err.get("param"))
                raise IncompleteResponse("stream ended without response.completed", request_id=rid)
        except httpx.TransportError as e:
            raise TemporarilyUnavailable(f"connection failed: {type(e).__name__}") from e
