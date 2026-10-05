"""AI tests: the concept-extraction prompt yields the JSON structure the pipeline needs.

Mocked (CI): golden model outputs — including the messy shapes real models produce —
are parsed into valid concepts. Live (opt-in, CI-excluded): set ``LIVE_LLM_TESTS=1`` plus
``LLM_API_KEY`` (and optionally ``LLM_BASE_URL``/``LIVE_LLM_MODEL``) to send the real
prompt to a provider and validate the structure of its answer (TEST_STRATEGY §3.3, §7).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from app.ai.cost_tracker import AICallContext
from app.ai.llm_client import LLMClient
from app.ai.output import extract_json
from app.ai.prompt_manager import get_prompt_manager
from app.config import ContentProcessingSettings
from app.services.content.chunker import Chunk
from app.services.content.concept_extractor import (
    ConceptExtractionOutput,
    ConceptExtractor,
    parse_extraction,
)
from app.services.content.concept_graph import RelationshipOutput, coerce_relationship_output
from tests.fakes import FakeLLMProvider, MemoryRecorder
from tests.support import build_settings

MATERIAL = (
    "5.1 Solving quadratic equations. A quadratic equation has the form ax^2 + bx + c = 0. "
    "It can be solved by factoring when the expression splits into two linear factors. "
    "When factoring is hard, use the quadratic formula x = (-b ± sqrt(b^2 - 4ac)) / 2a. "
    "The discriminant b^2 - 4ac tells how many real roots exist: positive means two, "
    "zero means one repeated root, negative means no real roots."
)
CHUNKS = [Chunk(index=0, content=MATERIAL, page_numbers=[12], heading="5.1", token_count=90)]

GOLDEN_OUTPUTS: list[str] = [
    # Clean JSON-mode answer
    '{"concepts": [{"name": "Quadratic Formula", "description": "Solves any quadratic.", '
    '"difficulty_estimate": "medium", "prerequisites": ["Factoring Quadratics"], '
    '"relationships": [{"concept": "Discriminant", "type": "related"}], "chunks": [1]}]}',
    # Fenced block + chatter (providers without JSON mode)
    'Here are the concepts:\n```json\n{"concepts": [{"name": "Discriminant", '
    '"description": "b^2-4ac", "difficulty_estimate": 0.6, "chunks": ["1"]}]}\n```',
    # Bare array, unknown keys, some invalid entries
    '[{"name": "Factoring Quadratics", "extra": true}, {"name": ""}, {"oops": 1}]',
]


def assert_valid(concepts: list[Any]) -> None:
    assert concepts, "at least one concept"
    for c in concepts:
        assert 2 <= len(c.name) <= 200
        assert 0.0 <= c.difficulty_estimate <= 1.0
        assert all(isinstance(p, str) and p for p in c.prerequisites)
        assert all(
            r.type in ("related", "generalisation", "specialisation") for r in c.relationships
        )
        assert all(1 <= n <= len(CHUNKS) for n in c.chunks)


@pytest.mark.parametrize("raw", GOLDEN_OUTPUTS)
def test_golden_outputs_parse_to_valid_structure(raw: str) -> None:
    concepts = parse_extraction(extract_json(raw), chunk_count=1, max_concepts=10)
    assert_valid(concepts)
    ConceptExtractionOutput(concepts=concepts)  # pipeline schema accepts it


async def test_extractor_end_to_end_with_golden_output() -> None:
    fake = FakeLLMProvider(raw_response=GOLDEN_OUTPUTS[0])
    llm = LLMClient(build_settings(), MemoryRecorder(), providers={"fake": fake})  # type: ignore[arg-type]
    extractor = ConceptExtractor(llm, get_prompt_manager(), ContentProcessingSettings())
    ctx = AICallContext(user_id=uuid.uuid4(), trace_id=uuid.uuid4(), purpose="test")
    found = await extractor.extract(CHUNKS, ctx, subject_name="Mathematics")
    qf = found["quadratic formula"]
    assert qf.prerequisites == {"factoring quadratic"}
    assert qf.related == {"discriminant": "related"}
    assert qf.sections == {0: 1.0}


def test_golden_relationship_output() -> None:
    raw = (
        '{"duplicates": [{"new": "N2", "existing": "E1"}], "relationships": ['
        '{"source": "E1", "target": "N1", "type": "prerequisite", "strength": 0.9}]}'
    )
    out = RelationshipOutput.model_validate(coerce_relationship_output(extract_json(raw)))
    assert out.duplicates[0].existing == "E1"
    assert out.relationships[0].type == "prerequisite"


LIVE = os.environ.get("LIVE_LLM_TESTS") == "1" and bool(os.environ.get("LLM_API_KEY"))


@pytest.mark.skipif(not LIVE, reason="live LLM tests are opt-in (LIVE_LLM_TESTS=1 + LLM_API_KEY)")
async def test_live_provider_returns_valid_structure() -> None:
    model = os.environ.get("LIVE_LLM_MODEL", "gpt-4o-mini")
    settings = build_settings(
        llm_api_key=os.environ["LLM_API_KEY"],
        llm_base_url=os.environ.get("LLM_BASE_URL", ""),
        llm_tasks={"concept_extraction": {"provider": "openai", "model": model}},
    )
    llm = LLMClient(settings, MemoryRecorder())  # type: ignore[arg-type]
    try:
        extractor = ConceptExtractor(llm, get_prompt_manager(), ContentProcessingSettings())
        ctx = AICallContext(user_id=uuid.uuid4(), trace_id=uuid.uuid4(), purpose="live-test")
        found = await extractor.extract(CHUNKS, ctx, subject_name="Mathematics")
    finally:
        await llm.aclose()
    assert 1 <= len(found) <= 15
    for cand in found.values():
        assert cand.name and 0.0 <= cand.difficulty <= 1.0
