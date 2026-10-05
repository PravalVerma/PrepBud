"""Task-oriented LLM client: config-driven routing, auditing every call, JSON parsing."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from app.ai.cost_tracker import AICallContext, compute_cost, cost_key, sha256
from app.ai.llm_client import LLMClient, default_provider_factory
from app.ai.output import extract_json
from app.ai.providers.base import (
    AIBudgetExceededError,
    LLMMessage,
    LLMOutputError,
    LLMResponseError,
    LLMServerError,
)
from app.ai.providers.openai import OpenAICompatibleProvider
from app.config import ModelPricing
from tests.fakes import FakeLLMProvider, MemoryRecorder
from tests.support import build_settings

USER = uuid.uuid4()
MSG = [LLMMessage(role="user", content="hello")]


def ctx(purpose: str = "test") -> AICallContext:
    return AICallContext(user_id=USER, trace_id=uuid.uuid4(), purpose=purpose)


def client(
    fake: FakeLLMProvider | None = None, **recorder: Any
) -> tuple[LLMClient, MemoryRecorder, FakeLLMProvider]:
    fake = fake or FakeLLMProvider()
    rec = MemoryRecorder(**recorder)
    return LLMClient(build_settings(), rec, providers={"fake": fake}), rec, fake  # type: ignore[arg-type]


class Answer(BaseModel):
    answer: int


class TestComplete:
    async def test_uses_task_config_and_records(self) -> None:
        llm, rec, _ = client(FakeLLMProvider(raw_response='{"answer": 4}'))
        out = await llm.complete("question_generation", MSG, ctx("generate_question"))

        assert out.model == "fake-question_generation"
        [(c, record)] = rec.records
        assert c.purpose == "generate_question"
        assert record.status == "success"
        assert record.provider == "fake"
        assert record.response == '{"answer": 4}'
        assert record.request == [{"role": "user", "content": "hello"}]
        assert record.input_tokens > 0

    async def test_failure_is_recorded_then_raised(self) -> None:
        fake = FakeLLMProvider(fail_with=LLMServerError("down"), fail_times=-1)
        llm, rec, _ = client(fake)
        with pytest.raises(LLMServerError):
            await llm.complete("session_summary", MSG, ctx())
        [(_, record)] = rec.records
        assert record.status == "error"
        assert record.error_message == "down"
        assert record.response is None

    async def test_budget_checked_before_calling(self) -> None:
        llm, rec, fake = client(budget_exceeded=True)
        with pytest.raises(AIBudgetExceededError):
            await llm.complete("session_summary", MSG, ctx())
        assert fake.calls == [] and rec.records == []

    async def test_unknown_task_or_provider(self) -> None:
        llm, _, _ = client()
        with pytest.raises(LLMResponseError, match="No LLM configuration"):
            await llm.complete("nope", MSG, ctx())
        with pytest.raises(LLMResponseError, match="not configured"):
            default_provider_factory("ghost", build_settings())

    async def test_default_factory_builds_openai_compatible(self) -> None:
        settings = build_settings(
            llm_base_url="http://localhost:11434/v1", llm_default_provider="ollama"
        )
        provider = default_provider_factory("ollama", settings)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.config.base_url == "http://localhost:11434/v1"
        await provider.aclose()

    async def test_providers_are_cached_and_closed(self) -> None:
        built: list[str] = []

        def factory(name: str, settings: Any) -> FakeLLMProvider:
            built.append(name)
            return FakeLLMProvider(name=name)

        llm = LLMClient(build_settings(), MemoryRecorder(), provider_factory=factory)  # type: ignore[arg-type]
        await llm.complete("session_summary", MSG, ctx())
        await llm.complete("session_summary", MSG, ctx())
        assert built == ["fake"]
        await llm.aclose()


class TestCompleteJson:
    async def test_parses_into_schema(self) -> None:
        llm, _, _ = client(FakeLLMProvider(raw_response='```json\n{"answer": 42}\n```'))
        assert (await llm.complete_json("answer_evaluation", MSG, ctx(), Answer)).answer == 42

    async def test_retries_once_with_feedback(self) -> None:
        replies = iter(["not json at all", '{"answer": 7}'])
        seen: list[list[LLMMessage]] = []

        def respond(messages: list[LLMMessage]) -> str:
            seen.append(messages)
            return next(replies)

        llm, rec, _ = client(FakeLLMProvider(on_complete=respond))
        out = await llm.complete_json("answer_evaluation", MSG, ctx(), Answer)

        assert out.answer == 7
        assert len(rec.records) == 2  # both calls audited
        retry = seen[1]
        assert retry[-2].role == "assistant" and retry[-2].content == "not json at all"
        assert "not valid JSON" in retry[-1].content

    async def test_gives_up_after_retries(self) -> None:
        llm, _, _ = client(FakeLLMProvider(raw_response='{"answer": "many"}'))
        with pytest.raises(LLMOutputError):
            await llm.complete_json("answer_evaluation", MSG, ctx(), Answer, retries=1)

    async def test_coerce_hook(self) -> None:
        llm, _, _ = client(FakeLLMProvider(raw_response="[5]"))
        out = await llm.complete_json(
            "answer_evaluation", MSG, ctx(), Answer, coerce=lambda raw: {"answer": raw[0]}
        )
        assert out.answer == 5


class TestStreamAndEmbed:
    async def test_stream_yields_and_records_once(self) -> None:
        llm, rec, _ = client()
        parts = [p async for p in llm.stream("tutor_explanation", MSG, ctx("explain"))]
        assert "".join(parts) == "Hello student!"
        [(_, record)] = rec.records
        assert record.response == "Hello student!"
        assert (record.input_tokens, record.output_tokens) == (10, 3)
        assert record.extra["streamed"] is True

    async def test_embed_records_summary_not_content(self) -> None:
        llm, rec, _ = client()
        vectors = await llm.embed(["alpha beta", "gamma"], ctx("embed"))
        assert len(vectors) == 2 and len(vectors[0]) == 64
        [(_, record)] = rec.records
        assert record.request["inputs"] == 2
        assert record.request["input_sha256"] == sha256("alpha beta\x1egamma")
        assert record.response is None

    async def test_embed_empty_is_free(self) -> None:
        llm, rec, _ = client()
        assert await llm.embed([], ctx()) == []
        assert rec.records == []

    async def test_embed_failure_recorded(self) -> None:
        llm, rec, _ = client(FakeLLMProvider(embed_fail_with=LLMServerError("x")))
        with pytest.raises(LLMServerError):
            await llm.embed(["a"], ctx())
        assert rec.records[0][1].status == "error"


class TestCost:
    PRICING = {
        "gpt-4o": ModelPricing(input_per_1m=2.5, output_per_1m=10.0),
        "gpt-4o-mini": ModelPricing(input_per_1m=0.15, output_per_1m=0.6),
    }

    def test_compute_cost(self) -> None:
        assert compute_cost(self.PRICING, "gpt-4o", 1_000_000, 100_000) == pytest.approx(3.5)

    def test_dated_variant_uses_longest_prefix(self) -> None:
        cost = compute_cost(self.PRICING, "gpt-4o-mini-2024-07-18", 1_000_000, 0)
        assert cost == pytest.approx(0.15)

    def test_unknown_model(self) -> None:
        assert compute_cost(self.PRICING, "llama3", 10, 10) is None

    def test_cost_key_is_per_user_per_day(self) -> None:
        assert cost_key(USER).startswith(f"cost:{USER}:20")


class TestExtractJson:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('{"a": 1}', {"a": 1}),
            ("  [1, 2] ", [1, 2]),
            ('```json\n{"a": 2}\n```', {"a": 2}),
            ('Sure! Here it is: {"a": 3} hope that helps', {"a": 3}),
            ('noise { not json } then {"a": 4}', {"a": 4}),
        ],
    )
    def test_extracts(self, text: str, expected: Any) -> None:
        assert extract_json(text) == expected

    @pytest.mark.parametrize("text", ["", "no json here", "{broken"])
    def test_rejects(self, text: str) -> None:
        with pytest.raises(LLMOutputError):
            extract_json(text)


def test_context_for_purpose_copies() -> None:
    base = AICallContext(user_id=USER, trace_id=uuid.uuid4(), purpose="a", metadata={"k": 1})
    child = base.for_purpose("b", prompt_name="p", prompt_version="1")
    child.metadata["k"] = 2
    assert (child.purpose, child.prompt_name, child.prompt_version) == ("b", "p", "1")
    assert base.metadata == {"k": 1} and child.trace_id == base.trace_id
