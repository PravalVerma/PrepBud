"""Provider-agnostic LLM interface (AI_SYSTEM_DESIGN §2.1, ADR-003).

Application code never talks to a provider directly: it goes through
`app.ai.llm_client.LLMClient`, which picks the provider + model for a task from
configuration and records every call for audit and cost tracking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    provider: str
    finish_reason: str | None = None
    # True when the provider returned no usage block and tokens were estimated.
    usage_estimated: bool = False


class EmbeddingResponse(BaseModel):
    vectors: list[list[float]]
    model: str
    input_tokens: int
    latency_ms: int
    provider: str
    usage_estimated: bool = False


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One streamed delta; the final event may carry token usage instead of text."""

    delta: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None


# --- Errors ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base class for provider failures. ``retryable`` drives task-level retries."""

    retryable: bool = False
    status: Literal["error", "timeout"] = "error"
    code: str = "LLM_ERROR"


class LLMTimeoutError(LLMError):
    retryable = True
    status = "timeout"
    code = "LLM_TIMEOUT"


class LLMRateLimitError(LLMError):
    retryable = True
    code = "LLM_RATE_LIMITED"


class LLMQuotaExceededError(LLMError):
    """The provider's daily quota for this model is used up — retrying today won't help."""

    code = "LLM_QUOTA_EXCEEDED"


class LLMServerError(LLMError):
    retryable = True
    code = "LLM_UNAVAILABLE"


class LLMResponseError(LLMError):
    """The provider rejected the request or answered with something unusable."""

    code = "LLM_BAD_RESPONSE"


class LLMOutputError(LLMError):
    """The model's text could not be parsed into the structure the caller asked for."""

    code = "LLM_INVALID_OUTPUT"


class AIBudgetExceededError(LLMError):
    """The user's daily AI budget is spent (SECURITY_MODEL §6.3)."""

    code = "AI_BUDGET_EXCEEDED"


# --- Provider ---------------------------------------------------------------------------


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> LLMResponse: ...

    @abstractmethod
    def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> AsyncIterator[StreamEvent]: ...

    @abstractmethod
    async def embed(
        self, texts: list[str], *, model: str, dimensions: int | None = None
    ) -> EmbeddingResponse: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release network resources."""


def estimate_tokens(text: str) -> int:
    """Provider-independent token estimate (~4 characters per token for English)."""
    return (len(text) + 3) // 4
