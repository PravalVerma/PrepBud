"""OpenAI-compatible provider (Chat Completions + Embeddings over HTTP).

Speaks the de-facto standard API that OpenAI, Ollama, vLLM, LM Studio, Together,
Groq, OpenRouter, Azure-style gateways, etc. all expose, so any of them is a
configuration change away (AC-3.7). Transient failures (timeouts, 429, 5xx) are
retried with exponential backoff, honouring ``Retry-After``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from app.ai.providers.base import (
    EmbeddingResponse,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMServerError,
    LLMTimeoutError,
    StreamEvent,
    estimate_tokens,
)
from app.config import LLMProviderConfig

MAX_RETRY_AFTER_SECONDS = 20.0


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return min(float(value), MAX_RETRY_AFTER_SECONDS) if value else None
    except ValueError:
        return None


def _error_for(response: httpx.Response) -> LLMError:
    try:
        detail = response.json().get("error", {})
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
    except (ValueError, AttributeError):
        message = None
    text = f"Provider returned HTTP {response.status_code}" + (f": {message}" if message else "")
    if response.status_code == 429:
        return LLMRateLimitError(text)
    if response.status_code >= 500:
        return LLMServerError(text)
    return LLMResponseError(text)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        config: LLMProviderConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.name = name
        self.config = config
        self._sleep = sleep
        headers = {"Content-Type": "application/json"}
        if api_key := config.api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=httpx.Timeout(config.timeout_seconds, connect=10.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- transport with retries ------------------------------------------------------

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        attempt = 0
        while True:
            delay: float | None
            try:
                response = await self._client.post(path, json=payload)
            except httpx.TimeoutException as exc:
                error: LLMError = LLMTimeoutError(f"Provider timed out: {type(exc).__name__}")
                delay = None
            except httpx.TransportError as exc:
                error = LLMServerError(f"Provider unreachable: {type(exc).__name__}")
                delay = None
            else:
                if response.is_success:
                    return response
                error = _error_for(response)
                delay = _retry_after(response)
            if not error.retryable or attempt >= self.config.max_retries:
                raise error
            await self._sleep(delay if delay is not None else 0.5 * 2**attempt)
            attempt += 1

    # --- API ---------------------------------------------------------------------------

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and self.config.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        start = time.monotonic()
        response = await self._post("chat/completions", payload)
        latency_ms = int((time.monotonic() - start) * 1000)
        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMResponseError("Malformed chat completion response") from exc

        usage = body.get("usage") or {}
        estimated = "prompt_tokens" not in usage
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        if estimated:
            input_tokens = sum(estimate_tokens(m.content) for m in messages)
            output_tokens = estimate_tokens(content)
        return LLMResponse(
            content=content,
            model=str(body.get("model") or model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            provider=self.name,
            finish_reason=choice.get("finish_reason"),
            usage_estimated=estimated,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> AsyncIterator[StreamEvent]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            async with self._client.stream("POST", "chat/completions", json=payload) as response:
                if not response.is_success:
                    await response.aread()
                    raise _error_for(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError as exc:
                        raise LLMResponseError("Malformed stream chunk") from exc
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield StreamEvent(delta=delta)
                    if usage := chunk.get("usage"):
                        yield StreamEvent(
                            input_tokens=int(usage.get("prompt_tokens") or 0),
                            output_tokens=int(usage.get("completion_tokens") or 0),
                            model=chunk.get("model"),
                        )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Provider stream timed out") from exc
        except httpx.TransportError as exc:
            raise LLMServerError("Provider stream interrupted") from exc

    async def embed(
        self, texts: list[str], *, model: str, dimensions: int | None = None
    ) -> EmbeddingResponse:
        payload: dict[str, Any] = {"model": model, "input": texts}
        if dimensions and self.config.supports_dimensions_param:
            payload["dimensions"] = dimensions

        start = time.monotonic()
        response = await self._post("embeddings", payload)
        latency_ms = int((time.monotonic() - start) * 1000)
        try:
            body = response.json()
            items = sorted(body["data"], key=lambda d: d.get("index", 0))
            vectors = [[float(x) for x in item["embedding"]] for item in items]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LLMResponseError("Malformed embeddings response") from exc
        if len(vectors) != len(texts):
            raise LLMResponseError(f"Expected {len(texts)} embeddings, got {len(vectors)}")
        if dimensions and any(len(v) != dimensions for v in vectors):
            raise LLMResponseError(
                f"Embedding size mismatch: expected {dimensions} (check the embedding task config)"
            )

        usage = body.get("usage") or {}
        estimated = "prompt_tokens" not in usage
        input_tokens = (
            sum(estimate_tokens(t) for t in texts)
            if estimated
            else int(usage.get("prompt_tokens") or 0)
        )
        return EmbeddingResponse(
            vectors=vectors,
            model=str(body.get("model") or model),
            input_tokens=input_tokens,
            latency_ms=latency_ms,
            provider=self.name,
            usage_estimated=estimated,
        )
