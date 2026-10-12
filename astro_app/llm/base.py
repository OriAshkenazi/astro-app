"""LLM interface used by the reading workflow, independent of the provider."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMRequest:
    instructions: str                    # developer instructions (never a system-role message)
    input: str | list[dict]              # user content, or a full input item list
    schema: dict | None = None           # JSON schema for structured output (strict)
    schema_name: str = "output"
    purpose: str = ""                    # e.g. "interpret:s07", for logs and the fake client


@dataclass
class LLMResult:
    text: str
    data: Any = None                     # parsed JSON when a schema was requested
    model: str | None = None
    request_id: str | None = None
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Model:
    slug: str
    display_name: str


class LLMClient(Protocol):
    async def complete(self, req: LLMRequest) -> LLMResult: ...

    async def list_models(self) -> list[Model]: ...


# ----------------------------------------------------------------- errors
class LLMError(Exception):
    retryable = False
    user_message = "The AI request failed."

    def __init__(self, message: str = "", *, status: int | None = None, code: str | None = None,
                 request_id: str | None = None, param: str | None = None):
        self.status, self.code, self.request_id, self.param = status, code, request_id, param
        super().__init__(f"{message} (status={status} code={code} request_id={request_id})")


class UsageLimitExceeded(LLMError):
    """Pause new plan requests; the user can review limits in ChatGPT Settings → Usage."""
    user_message = ("Your ChatGPT plan usage limit for this app has been reached. Generation is paused; "
                    "review limits in ChatGPT Settings → Usage, then resume.")


class PlanNotEligible(LLMError):
    user_message = "ChatGPT plan usage is not available for this account or workspace."


class TemporarilyUnavailable(LLMError):
    retryable = True
    user_message = "ChatGPT is temporarily unavailable. The request will be retried."


class AuthRejected(LLMError):
    user_message = "ChatGPT did not accept this sign-in. Please sign in again."


class AccessForbidden(LLMError):
    user_message = "ChatGPT refused this request (policy, region or permission)."


class UnsupportedRequest(LLMError):
    user_message = "The app sent a request ChatGPT plan usage does not support. Please report this bug."


class IncompleteResponse(LLMError):
    retryable = True
    user_message = "The AI response was cut off. The request will be retried."


class InvalidOutput(LLMError):
    retryable = True
    user_message = "The AI returned malformed output. The request will be retried."
