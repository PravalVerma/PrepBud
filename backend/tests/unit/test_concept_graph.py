"""Concept graph building: cycle-free prerequisites, dedup, relationship parsing."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.ai.cost_tracker import AICallContext
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import get_prompt_manager
from app.ai.providers.base import LLMMessage, LLMServerError
from app.config import ContentProcessingSettings
from app.services.content.concept_extractor import CandidateConcept
from app.services.content.concept_graph import (
    ConceptGraphBuilder,
    Edge,
    ExistingConcept,
    coerce_relationship_output,
    filter_edges,
)
from tests.fakes import FakeLLMProvider, MemoryRecorder

A, B, C, D = (uuid.uuid4() for _ in range(4))


def prereq(s: uuid.UUID, t: uuid.UUID, strength: float = 0.8) -> Edge:
    return Edge(s, t, "prerequisite", strength)


class TestFilterEdges:
    def test_accepts_dag(self) -> None:
        accepted, dropped = filter_edges([prereq(A, B), prereq(B, C), prereq(A, C)], [])
        assert len(accepted) == 3 and dropped == []

    def test_drops_edge_closing_a_cycle_keeping_stronger(self) -> None:
        accepted, dropped = filter_edges(
            [prereq(A, B, 0.9), prereq(B, C, 0.8), prereq(C, A, 0.3)], []
        )
        assert prereq(C, A, 0.3) in dropped
        assert {(e.source, e.target) for e in accepted} == {(A, B), (B, C)}

    def test_cycle_with_existing_graph(self) -> None:
        existing = [(A, B, "prerequisite"), (B, C, "prerequisite")]
        accepted, dropped = filter_edges([prereq(C, A)], existing)
        assert accepted == [] and dropped == [prereq(C, A)]

    def test_two_node_cycle(self) -> None:
        accepted, dropped = filter_edges([prereq(A, B, 0.9), prereq(B, A, 0.5)], [])
        assert accepted == [prereq(A, B, 0.9)] and dropped == [prereq(B, A, 0.5)]

    def test_self_loops_and_duplicates_removed(self) -> None:
        accepted, _ = filter_edges(
            [prereq(A, A), prereq(A, B, 0.4), prereq(A, B, 0.7), prereq(A, B, 0.6)], []
        )
        assert accepted == [prereq(A, B, 0.7)]

    def test_existing_edges_not_reinserted(self) -> None:
        accepted, _ = filter_edges([prereq(A, B)], [(A, B, "prerequisite")])
        assert accepted == []

    def test_related_is_symmetric(self) -> None:
        accepted, _ = filter_edges(
            [Edge(A, B, "related", 0.5), Edge(B, A, "related", 0.4)], [(C, D, "related")]
        )
        assert accepted == [Edge(A, B, "related", 0.5)]
        accepted, _ = filter_edges([Edge(D, C, "related", 0.5)], [(C, D, "related")])
        assert accepted == []

    def test_non_prerequisite_types_may_form_cycles(self) -> None:
        accepted, dropped = filter_edges(
            [Edge(A, B, "generalisation", 0.5), Edge(B, A, "specialisation", 0.5)], []
        )
        assert len(accepted) == 2 and dropped == []


class TestCoerceOutput:
    def test_drops_invalid_items(self) -> None:
        out = coerce_relationship_output(
            {
                "duplicates": [{"new": "N1", "existing": "E1"}, {"new": "N2"}],
                "relationships": [
                    {"source": "N1", "target": "N2", "type": "Prerequisite", "strength": 5},
                    {"source": "N1", "target": "N2", "type": "specialization", "strength": "x"},
                    {"source": "N1", "target": "N2", "type": "enemy"},
                    "junk",
                ],
            }
        )
        assert len(out["duplicates"]) == 1
        assert [(r.type, r.strength) for r in out["relationships"]] == [
            ("prerequisite", 1.0),
            ("specialisation", 0.8),
        ]

    def test_requires_object(self) -> None:
        with pytest.raises(ValueError, match="object"):
            coerce_relationship_output([1, 2])

    def test_missing_sections_default_empty(self) -> None:
        assert coerce_relationship_output({}) == {"duplicates": [], "relationships": []}


def cand(name: str, **kw: Any) -> CandidateConcept:
    from app.services.content.concept_extractor import normalize_name

    return CandidateConcept(key=normalize_name(name), name=name, description=f"{name} desc", **kw)


def builder(fake: FakeLLMProvider, **cfg: Any) -> ConceptGraphBuilder:
    from tests.support import build_settings

    llm = LLMClient(build_settings(), MemoryRecorder(), providers={"fake": fake})  # type: ignore[arg-type]
    return ConceptGraphBuilder(llm, get_prompt_manager(), ContentProcessingSettings(**cfg))


def ctx() -> AICallContext:
    return AICallContext(user_id=uuid.uuid4(), trace_id=uuid.uuid4(), purpose="document_ingest")


class TestPlanner:
    async def test_exact_duplicates_merge_without_llm_and_llm_edges_resolve(self) -> None:
        existing = ExistingConcept(uuid.uuid4(), "factoring", "Factoring")
        candidates = {c.key: c for c in [cand("Factoring"), cand("Quadratic Formula")]}

        def respond(messages: list[LLMMessage]) -> str:
            prompt = messages[-1].content
            assert "- E1: Factoring" in prompt
            assert "- N2: Quadratic Formula" in prompt
            return json.dumps(
                {
                    "duplicates": [],
                    "relationships": [
                        {"source": "E1", "target": "N2", "type": "prerequisite", "strength": 0.9},
                        {"source": "N9", "target": "N2", "type": "related"},
                    ],
                }
            )

        plan = await builder(FakeLLMProvider(on_complete=respond)).plan(
            candidates, [existing], ctx()
        )
        assert plan.merged == {"factoring": existing.id}
        qf = plan.concept_ids["quadratic formula"]
        assert qf != existing.id
        assert plan.edges == [Edge(existing.id, qf, "prerequisite", 0.9)]
        assert plan.relationship_detection == "complete"

    async def test_llm_duplicates_merge(self) -> None:
        existing = ExistingConcept(uuid.uuid4(), "pythagorean theorem", "Pythagorean Theorem")
        fake = FakeLLMProvider(duplicates={"Pythagoras Rule": "Pythagorean Theorem"})
        plan = await builder(fake).plan(
            {"pythagoras rule": cand("Pythagoras Rule")}, [existing], ctx()
        )
        assert plan.concept_ids["pythagoras rule"] == existing.id
        assert plan.merged == {"pythagoras rule": existing.id}

    async def test_extraction_hints_become_edges(self) -> None:
        a = cand("Limits")
        b = cand("Derivatives", prerequisites={"limit", "unknown thing"})
        b.related = {
            "tangent line": "related",
            "calculus": "generalisation",
            "power rule": "specialisation",
        }
        others = {c.key: c for c in [cand("Tangent Line"), cand("Calculus"), cand("Power Rule")]}
        plan = await builder(FakeLLMProvider(raw_response="{}")).plan(
            {a.key: a, b.key: b, **others}, [], ctx()
        )
        ids = plan.concept_ids
        assert Edge(ids["limit"], ids["derivative"], "prerequisite", 0.7) in plan.edges
        assert Edge(ids["derivative"], ids["tangent line"], "related", 0.5) in plan.edges
        assert Edge(ids["calculus"], ids["derivative"], "generalisation", 0.6) in plan.edges
        assert Edge(ids["power rule"], ids["derivative"], "specialisation", 0.6) in plan.edges

    async def test_relationship_output_failure_is_tolerated(self) -> None:
        plan = await builder(FakeLLMProvider(raw_response="garbage")).plan(
            {c.key: c for c in [cand("One"), cand("Two")]}, [], ctx()
        )
        assert plan.relationship_detection == "failed"
        assert len(plan.concept_ids) == 2

    async def test_transport_failures_propagate(self) -> None:
        fake = FakeLLMProvider(fail_with=LLMServerError("down"), fail_times=-1)
        with pytest.raises(LLMServerError):
            await builder(fake).plan({c.key: c for c in [cand("One"), cand("Two")]}, [], ctx())

    async def test_single_concept_without_context_skips_llm(self) -> None:
        fake = FakeLLMProvider()
        await builder(fake).plan({"solo": cand("Solo")}, [], ctx())
        assert fake.calls == []

    async def test_large_sets_are_batched(self) -> None:
        fake = FakeLLMProvider(raw_response='{"relationships": []}')
        candidates = {c.key: c for c in (cand(f"Concept {i}") for i in range(7))}
        await builder(fake, relationship_batch_size=3).plan(candidates, [], ctx())
        assert fake.calls.count("relationships") == 3
