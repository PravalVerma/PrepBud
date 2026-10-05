"""Task-oriented LLM client (ADR-003, AI_SYSTEM_DESIGN §2).

Callers name a *task* (``concept_extraction``, ``embedding``, …); the provider,
model and sampling parameters come from ``settings.llm_tasks[task]``. Every call:

1. checks the user's daily AI budget,
2. calls the provider,
3. records an `AIInteraction` (success *or* failure) with tokens, cost and latency.

Nothing here names a specific vendor — providers are built from configuration.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.ai.cost_tracker import AICallContext, AIUsageRecorder, InteractionRecord, sha256
from app.ai.output import extract_json
from app.ai.providers.base import (
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMProvider,
    LLMResponse,
    LLMResponseError,
    estimate_tokens,
)
from app.ai.providers.openai import OpenAICompatibleProvider
from app.config import LLMTaskConfig, Settings

ProviderFactory = Callable[[str, Settings], LLMProvider]


def default_provider_factory(name: str, settings: Settings) -> LLMProvider:
    try:
        config = settings.llm_provider(name)
    except KeyError as exc:
        raise LLMResponseError(f"LLM provider {name!r} is not configured") from exc
    return OpenAICompatibleProvider(name, config)


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        recorder: AIUsageRecorder,
        *,
        providers: dict[str, LLMProvider] | None = None,
        provider_factory: ProviderFactory = default_provider_factory,
    ) -> None:
        self.settings = settings
        self.recorder = recorder
        self._providers: dict[str, LLMProvider] = dict(providers or {})
        self._factory = provider_factory

    def task_config(self, task: str) -> LLMTaskConfig:
        try:
            return self.settings.llm_tasks[task]
        except KeyError as exc:
            raise LLMResponseError(f"No LLM configuration for task {task!r}") from exc

    def provider(self, name: str) -> LLMProvider:
        if name not in self._providers:
            self._providers[name] = self._factory(name, self.settings)
        return self._providers[name]

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()

    # --- completions ------------------------------------------------------------------

    async def complete(
        self,
        task: str,
        messages: list[LLMMessage],
        ctx: AICallContext,
        *,
        json_mode: bool | None = None,
    ) -> LLMResponse:
        cfg = self.task_config(task)
        provider = self.provider(cfg.provider)
        await self.recorder.check_budget(ctx.user_id)
        wants_json = cfg.response_format == "json" if json_mode is None else json_mode
        request = [m.model_dump() for m in messages]
        start = time.monotonic()
        try:
            response = await provider.complete(
                messages,
                model=cfg.model,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                json_mode=wants_json,
            )
        except LLMError as exc:
            await self.recorder.record(
                ctx,
                InteractionRecord(
                    provider=provider.name,
                    model=cfg.model,
                    task=task,
                    request=request,
                    response=None,
                    input_tokens=sum(estimate_tokens(m.content) for m in messages),
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status=exc.status,
                    error_message=str(exc)[:1000],
                ),
            )
            raise
        await self.recorder.record(
            ctx,
            InteractionRecord(
                provider=response.provider,
                model=response.model,
                task=task,
                request=request,
                response=response.content,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
                extra={"finish_reason": response.finish_reason}
                | ({"usage_estimated": True} if response.usage_estimated else {}),
            ),
        )
        return response

    async def complete_json[M: BaseModel](
        self,
        task: str,
        messages: list[LLMMessage],
        ctx: AICallContext,
        schema: type[M],
        *,
        coerce: Callable[[Any], Any] | None = None,
        retries: int = 1,
    ) -> M:
        """Complete and parse into ``schema``; on invalid output, re-ask with the error.

        ``coerce`` may reshape the raw JSON (e.g. wrap a bare list) before validation.
        """
        attempt_messages = list(messages)
        for attempt in range(retries + 1):
            response = await self.complete(task, attempt_messages, ctx, json_mode=True)
            try:
                raw = extract_json(response.content)
                return schema.model_validate(coerce(raw) if coerce else raw)
            except (LLMOutputError, PydanticValidationError, ValueError, TypeError) as exc:
                if attempt >= retries:
                    raise LLMOutputError(f"Invalid structured output for {task}: {exc}") from exc
                attempt_messages = [
                    *messages,
                    LLMMessage(role="assistant", content=response.content[:4000]),
                    LLMMessage(
                        role="user",
                        content=(
                            "That response was not valid JSON matching the requested schema "
                            f"({type(exc).__name__}). Reply again with only the JSON object."
                        ),
                    ),
                ]
        raise AssertionError("unreachable")  # pragma: no cover

    async def stream(
        self, task: str, messages: list[LLMMessage], ctx: AICallContext
    ) -> AsyncIterator[str]:
        """Stream text deltas; the interaction is recorded once the stream finishes."""
        cfg = self.task_config(task)
        provider = self.provider(cfg.provider)
        await self.recorder.check_budget(ctx.user_id)
        request = [m.model_dump() for m in messages]
        parts: list[str] = []
        input_tokens: int | None = None
        output_tokens: int | None = None
        model = cfg.model
        start = time.monotonic()
        status: Any = "success"
        error: str | None = None
        try:
            async for event in provider.stream(
                messages, model=cfg.model, temperature=cfg.temperature, max_tokens=cfg.max_tokens
            ):
                if event.delta:
                    parts.append(event.delta)
                    yield event.delta
                if event.input_tokens is not None:
                    input_tokens, output_tokens = event.input_tokens, event.output_tokens
                    model = event.model or model
        except LLMError as exc:
            status, error = exc.status, str(exc)[:1000]
            raise
        finally:
            content = "".join(parts)
            estimated = input_tokens is None
            await self.recorder.record(
                ctx,
                InteractionRecord(
                    provider=provider.name,
                    model=model,
                    task=task,
                    request=request,
                    response=content,
                    input_tokens=input_tokens
                    if input_tokens is not None
                    else sum(estimate_tokens(m.content) for m in messages),
                    output_tokens=output_tokens
                    if output_tokens is not None
                    else estimate_tokens(content),
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status=status,
                    error_message=error,
                    extra={"streamed": True} | ({"usage_estimated": True} if estimated else {}),
                ),
            )

    # --- embeddings -------------------------------------------------------------------

    async def embed(
        self, texts: list[str], ctx: AICallContext, *, task: str = "embedding"
    ) -> list[list[float]]:
        if not texts:
            return []
        cfg = self.task_config(task)
        provider = self.provider(cfg.provider)
        await self.recorder.check_budget(ctx.user_id)
        # Inputs are already stored (document sections / search query); log a summary.
        request = {"inputs": len(texts), "characters": sum(len(t) for t in texts)}
        start = time.monotonic()
        try:
            response = await provider.embed(texts, model=cfg.model, dimensions=cfg.dimensions)
        except LLMError as exc:
            await self.recorder.record(
                ctx,
                InteractionRecord(
                    provider=provider.name,
                    model=cfg.model,
                    task=task,
                    request=request,
                    response=None,
                    input_tokens=sum(estimate_tokens(t) for t in texts),
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status=exc.status,
                    error_message=str(exc)[:1000],
                ),
            )
            raise
        await self.recorder.record(
            ctx,
            InteractionRecord(
                provider=response.provider,
                model=response.model,
                task=task,
                request=request | {"input_sha256": sha256("".join(texts))},
                response=None,
                input_tokens=response.input_tokens,
                latency_ms=response.latency_ms,
                extra={"vectors": len(response.vectors)}
                | ({"usage_estimated": True} if response.usage_estimated else {}),
            ),
        )
        return response.vectors
