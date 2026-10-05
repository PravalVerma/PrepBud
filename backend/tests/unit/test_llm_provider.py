"""OpenAI-compatible provider over HTTP (ADR-003, AC-3.7) — exercised with httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.ai.providers.base import (
    LLMMessage,
    LLMRateLimitError,
    LLMResponseError,
    LLMServerError,
    LLMTimeoutError,
)
from app.ai.providers.openai import OpenAICompatibleProvider
from app.config import LLMProviderConfig

MESSAGES = [
    LLMMessage(role="system", content="You are helpful."),
    LLMMessage(role="user", content="Say hi"),
]


def chat_body(content: str = "hi", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "served-model",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }
    body.update(extra)
    return body


class Recorder:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def json(self, i: int = -1) -> dict[str, Any]:
        return dict(json.loads(self.requests[i].content))


def provider(
    handler: Callable[[httpx.Request], httpx.Response], **config: Any
) -> tuple[OpenAICompatibleProvider, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    cfg = LLMProviderConfig(base_url="https://llm.example/v1", api_key="sk-test", **config)
    return (
        OpenAICompatibleProvider(
            "acme", cfg, transport=httpx.MockTransport(handler), sleep=fake_sleep
        ),
        sleeps,
    )


class TestComplete:
    async def test_request_shape_and_response(self) -> None:
        rec = Recorder([httpx.Response(200, json=chat_body("hello"))])
        llm, _ = provider(rec)

        out = await llm.complete(MESSAGES, model="m-1", temperature=0.2, max_tokens=50)

        req = rec.requests[0]
        assert str(req.url) == "https://llm.example/v1/chat/completions"
        assert req.headers["authorization"] == "Bearer sk-test"
        assert rec.json() == {
            "model": "m-1",
            "messages": [m.model_dump() for m in MESSAGES],
            "temperature": 0.2,
            "max_tokens": 50,
        }
        assert out.content == "hello"
        assert out.model == "served-model"
        assert (out.input_tokens, out.output_tokens) == (12, 3)
        assert out.provider == "acme"
        assert out.finish_reason == "stop"
        assert out.usage_estimated is False

    async def test_json_mode(self) -> None:
        rec = Recorder([httpx.Response(200, json=chat_body("{}"))] * 2)
        llm, _ = provider(rec)
        await llm.complete(MESSAGES, model="m", json_mode=True)
        assert rec.json()["response_format"] == {"type": "json_object"}

        no_json, _ = provider(rec, supports_json_mode=False)
        await no_json.complete(MESSAGES, model="m", json_mode=True)
        assert "response_format" not in rec.json()

    async def test_no_api_key_sends_no_auth_header(self) -> None:
        rec = Recorder([httpx.Response(200, json=chat_body())])
        llm = OpenAICompatibleProvider(
            "local",
            LLMProviderConfig(base_url="http://localhost:11434/v1"),
            transport=httpx.MockTransport(rec),
        )
        await llm.complete(MESSAGES, model="llama3")
        assert "authorization" not in rec.requests[0].headers
        assert str(rec.requests[0].url) == "http://localhost:11434/v1/chat/completions"

    async def test_missing_usage_is_estimated(self) -> None:
        body = chat_body("four score and seven")
        del body["usage"]
        llm, _ = provider(Recorder([httpx.Response(200, json=body)]))
        out = await llm.complete(MESSAGES, model="m")
        assert out.usage_estimated is True
        assert out.input_tokens > 0 and out.output_tokens > 0

    async def test_null_content_becomes_empty(self) -> None:
        body = chat_body()
        body["choices"][0]["message"]["content"] = None
        llm, _ = provider(Recorder([httpx.Response(200, json=body)]))
        assert (await llm.complete(MESSAGES, model="m")).content == ""

    @pytest.mark.parametrize("body", [{"choices": []}, {"nope": 1}, "not json"])
    async def test_malformed_response(self, body: Any) -> None:
        resp = (
            httpx.Response(200, text=body)
            if isinstance(body, str)
            else httpx.Response(200, json=body)
        )
        llm, _ = provider(Recorder([resp]))
        with pytest.raises(LLMResponseError):
            await llm.complete(MESSAGES, model="m")


class TestRetries:
    async def test_retries_server_errors_with_backoff(self) -> None:
        rec = Recorder(
            [httpx.Response(500), httpx.Response(503), httpx.Response(200, json=chat_body("ok"))]
        )
        llm, sleeps = provider(rec, max_retries=2)
        assert (await llm.complete(MESSAGES, model="m")).content == "ok"
        assert sleeps == [0.5, 1.0]

    async def test_honours_retry_after_capped(self) -> None:
        rec = Recorder(
            [
                httpx.Response(429, headers={"retry-after": "3"}),
                httpx.Response(429, headers={"retry-after": "999"}),
                httpx.Response(200, json=chat_body()),
            ]
        )
        llm, sleeps = provider(rec, max_retries=2)
        await llm.complete(MESSAGES, model="m")
        assert sleeps == [3.0, 20.0]

    async def test_gives_up_after_max_retries(self) -> None:
        llm, sleeps = provider(Recorder([httpx.Response(429)] * 3), max_retries=2)
        with pytest.raises(LLMRateLimitError):
            await llm.complete(MESSAGES, model="m")
        assert len(sleeps) == 2

    async def test_timeouts_and_connection_errors(self) -> None:
        llm, _ = provider(Recorder([httpx.ReadTimeout("slow")]), max_retries=0)
        with pytest.raises(LLMTimeoutError) as exc:
            await llm.complete(MESSAGES, model="m")
        assert exc.value.retryable and exc.value.status == "timeout"

        llm, _ = provider(Recorder([httpx.ConnectError("refused")]), max_retries=0)
        with pytest.raises(LLMServerError):
            await llm.complete(MESSAGES, model="m")

    @pytest.mark.parametrize("status", [400, 401, 404])
    async def test_client_errors_not_retried(self, status: int) -> None:
        body = {"error": {"message": "bad key"}}
        llm, sleeps = provider(Recorder([httpx.Response(status, json=body)]), max_retries=3)
        with pytest.raises(LLMResponseError, match="bad key"):
            await llm.complete(MESSAGES, model="m")
        assert sleeps == []


class TestStream:
    async def test_parses_sse_deltas_and_usage(self) -> None:
        lines = [
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}, "model": "m-x"},
        ]
        sse = (
            "".join(f"data: {json.dumps(x)}\n\n" for x in lines)
            + ": keep-alive\n\ndata: [DONE]\n\n"
        )
        rec = Recorder([httpx.Response(200, text=sse)])
        llm, _ = provider(rec)

        events = [e async for e in llm.stream(MESSAGES, model="m")]

        assert "".join(e.delta for e in events) == "Hello"
        assert events[-1].input_tokens == 7 and events[-1].model == "m-x"
        body = rec.json()
        assert body["stream"] is True and body["stream_options"] == {"include_usage": True}

    async def test_stream_http_error(self) -> None:
        llm, _ = provider(Recorder([httpx.Response(503, json={"error": "down"})]))
        with pytest.raises(LLMServerError):
            async for _ in llm.stream(MESSAGES, model="m"):
                pass

    async def test_stream_malformed_chunk(self) -> None:
        llm, _ = provider(Recorder([httpx.Response(200, text="data: {oops\n\n")]))
        with pytest.raises(LLMResponseError):
            async for _ in llm.stream(MESSAGES, model="m"):
                pass

    async def test_stream_timeout(self) -> None:
        llm, _ = provider(Recorder([httpx.ReadTimeout("slow")]))
        with pytest.raises(LLMTimeoutError):
            async for _ in llm.stream(MESSAGES, model="m"):
                pass


class TestEmbed:
    async def test_orders_by_index_and_sends_dimensions(self) -> None:
        body = {
            "model": "emb",
            "data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}],
            "usage": {"prompt_tokens": 4},
        }
        rec = Recorder([httpx.Response(200, json=body)])
        llm, _ = provider(rec)

        out = await llm.embed(["a", "b"], model="emb", dimensions=2)

        assert out.vectors == [[1.0, 0.0], [0.0, 1.0]]
        assert out.input_tokens == 4
        assert rec.json() == {"model": "emb", "input": ["a", "b"], "dimensions": 2}
        assert str(rec.requests[0].url).endswith("/v1/embeddings")

    async def test_dimensions_param_can_be_disabled(self) -> None:
        body = {"data": [{"index": 0, "embedding": [0.5, 0.5]}]}
        rec = Recorder([httpx.Response(200, json=body)])
        llm, _ = provider(rec, supports_dimensions_param=False)
        out = await llm.embed(["a"], model="nomic", dimensions=2)
        assert "dimensions" not in rec.json()
        assert out.usage_estimated is True

    async def test_wrong_size_or_count_rejected(self) -> None:
        bad_size = {"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]}
        llm, _ = provider(Recorder([httpx.Response(200, json=bad_size)]))
        with pytest.raises(LLMResponseError, match="size mismatch"):
            await llm.embed(["a"], model="e", dimensions=2)

        llm, _ = provider(Recorder([httpx.Response(200, json={"data": []})]))
        with pytest.raises(LLMResponseError, match="Expected 1"):
            await llm.embed(["a"], model="e")

        llm, _ = provider(Recorder([httpx.Response(200, json={"data": "x"})]))
        with pytest.raises(LLMResponseError):
            await llm.embed(["a"], model="e")

    async def test_close(self) -> None:
        llm, _ = provider(Recorder([]))
        await llm.aclose()
