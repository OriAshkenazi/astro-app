"""Deterministic LLM client for tests, CI and offline development (no account, no network)."""
from __future__ import annotations

import json
from typing import Any, Callable

from .base import LLMRequest, LLMResult, Model

Responder = Callable[[LLMRequest], Any]


class FakeLLMClient:
    """`responder(req)` returns a str, a JSON-able object (for schema requests) or raises an LLMError.

    A list of replies is consumed in order; requests are recorded in `.requests`."""

    def __init__(self, responder: Responder | list | None = None, model: str = "fake-model"):
        self.model = model
        self.requests: list[LLMRequest] = []
        if isinstance(responder, list):
            queue = list(responder)
            responder = lambda req: queue.pop(0)  # noqa: E731
        self.responder = responder or (lambda req: {} if req.schema else "ok")

    async def list_models(self) -> list[Model]:
        return [Model(self.model, "Fake model")]

    async def complete(self, req: LLMRequest) -> LLMResult:
        self.requests.append(req)
        out = self.responder(req)
        if isinstance(out, BaseException):
            raise out
        if isinstance(out, str):
            data = json.loads(out) if req.schema is not None else None
            return LLMResult(text=out, data=data, model=self.model, request_id="fake")
        return LLMResult(text=json.dumps(out), data=out, model=self.model, request_id="fake")
