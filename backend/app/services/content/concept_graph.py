"""Concept graph building: de-duplication and relationship detection (AI_SYSTEM_DESIGN §5.1,
DOMAIN_MODEL aggregate 3, product assumption #7).

* Exact duplicates (same normalised name in the same subject) are merged without AI.
* The ``concept_extraction`` LLM task then sees the document's new concepts next to
  the student's existing ones and returns (a) further duplicates and (b) typed,
  weighted relationships, by reference id (``N3``, ``E7``) — never by fuzzy name.
* Prerequisite hints from per-chunk extraction are added too.
* Invariant: no circular prerequisite chains. Edges are accepted strongest-first and
  any prerequisite edge that would close a cycle (with the existing graph or the
  edges accepted so far) is dropped.

Edge direction: ``source`` is a prerequisite of ``target``.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.ai.cost_tracker import AICallContext
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import PromptManager
from app.ai.providers.base import LLMMessage, LLMOutputError
from app.config import ContentProcessingSettings
from app.core.logging import get_logger
from app.db.models.concept import RELATIONSHIP_TYPES
from app.services.content.concept_extractor import CandidateConcept

logger = get_logger(__name__)

TASK = "concept_extraction"
SYSTEM_PROMPT = "content/system"
RELATIONSHIP_PROMPT = "content/build_relationships"
_TYPE_ALIASES = {"generalization": "generalisation", "specialization": "specialisation"}


@dataclass(frozen=True, slots=True)
class ExistingConcept:
    id: uuid.UUID
    key: str
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Edge:
    source: uuid.UUID
    target: uuid.UUID
    type: str
    strength: float


@dataclass(slots=True)
class GraphPlan:
    #: candidate key -> concept id (a new uuid, or an existing concept it duplicates)
    concept_ids: dict[str, uuid.UUID]
    #: candidate keys merged into existing concepts
    merged: dict[str, uuid.UUID] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    relationship_detection: str = "complete"


# --- Lenient output schema -------------------------------------------------------------------


class _Duplicate(BaseModel):
    new: str
    existing: str


class _Relationship(BaseModel):
    source: str
    target: str
    type: str
    strength: float = 0.8

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v: Any) -> str:
        t = str(v).strip().lower()
        t = _TYPE_ALIASES.get(t, t)
        if t not in RELATIONSHIP_TYPES:
            raise ValueError(f"unknown relationship type {v!r}")
        return t

    @field_validator("strength", mode="before")
    @classmethod
    def _strength(cls, v: Any) -> float:
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.8


class RelationshipOutput(BaseModel):
    duplicates: list[_Duplicate] = Field(default_factory=list)
    relationships: list[_Relationship] = Field(default_factory=list)


def coerce_relationship_output(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("expected a JSON object")

    def valid(items: Any, model: type[BaseModel]) -> list[Any]:
        out = []
        for item in items if isinstance(items, list) else []:
            try:
                out.append(model.model_validate(item))
            except ValidationError:
                continue
        return out

    return {
        "duplicates": valid(raw.get("duplicates"), _Duplicate),
        "relationships": valid(raw.get("relationships"), _Relationship),
    }


# --- Graph algorithms -------------------------------------------------------------------------


def _reachable(adj: dict[uuid.UUID, set[uuid.UUID]], start: uuid.UUID, goal: uuid.UUID) -> bool:
    stack, seen = [start], {start}
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        for nxt in adj.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def _canonical_key(s: uuid.UUID, t: uuid.UUID, kind: str) -> tuple[uuid.UUID, uuid.UUID, str]:
    return (t, s, "generalisation") if kind == "specialisation" else (s, t, kind)


def canonical_edge(edge: Edge) -> Edge:
    """ "B specialisation A" → "A generalisation B" (same fact, one stored form)."""
    if edge.type == "specialisation":
        return Edge(edge.target, edge.source, "generalisation", edge.strength)
    return edge


def filter_edges(
    candidates: Iterable[Edge], existing: Iterable[tuple[uuid.UUID, uuid.UUID, str]]
) -> tuple[list[Edge], list[Edge]]:
    """Return ``(accepted, dropped)``: no self-loops, no duplicates, no prerequisite cycles.

    ``existing`` holds the edges already stored (source, target, type). "B specialisation A"
    states the same fact as "A generalisation B", so specialisation edges are stored in their
    generalisation form and each fact is kept once.
    """
    prereq: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    present: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
    for s, t, kind in existing:
        present.add(_canonical_key(s, t, kind))
        if kind == "prerequisite":
            prereq[s].add(t)
    candidates = [canonical_edge(e) for e in candidates]

    accepted: list[Edge] = []
    dropped: list[Edge] = []
    best: dict[tuple[uuid.UUID, uuid.UUID, str], Edge] = {}
    for e in candidates:
        k = (e.source, e.target, e.type)
        if e.source != e.target and (k not in best or e.strength > best[k].strength):
            best[k] = e
    for e in sorted(best.values(), key=lambda x: (-x.strength, str(x.source), str(x.target))):
        key = (e.source, e.target, e.type)
        reverse = (e.target, e.source, e.type)
        if key in present or (e.type == "related" and reverse in present):
            continue
        if e.type == "prerequisite" and _reachable(prereq, e.target, e.source):
            dropped.append(e)
            continue
        if e.type == "prerequisite":
            prereq[e.source].add(e.target)
        present.add(key)
        accepted.append(e)
    return accepted, dropped


# --- Planner -------------------------------------------------------------------------------------


def _short(text: str | None, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ConceptGraphBuilder:
    def __init__(
        self, llm: LLMClient, prompts: PromptManager, settings: ContentProcessingSettings
    ) -> None:
        self.llm = llm
        self.prompts = prompts
        self.settings = settings

    def build_messages(
        self,
        new: Sequence[tuple[str, CandidateConcept]],
        existing: Sequence[tuple[str, ExistingConcept]],
        subject_name: str | None,
    ) -> tuple[list[LLMMessage], str]:
        system = self.prompts.render(SYSTEM_PROMPT)
        prompt = self.prompts.render(
            RELATIONSHIP_PROMPT,
            subject_name=subject_name,
            new_concepts=[
                {"ref": ref, "name": c.name, "description": _short(c.description)} for ref, c in new
            ],
            existing_concepts=[
                {"ref": ref, "name": c.name, "description": _short(c.description)}
                for ref, c in existing
            ],
        )
        return [
            LLMMessage(role="system", content=system.text),
            LLMMessage(role="user", content=prompt.text),
        ], f"{system.version}+{prompt.version}"

    async def plan(
        self,
        candidates: dict[str, CandidateConcept],
        existing: Sequence[ExistingConcept],
        ctx: AICallContext,
        *,
        subject_name: str | None = None,
    ) -> GraphPlan:
        by_key = {e.key: e for e in existing}
        plan = GraphPlan(concept_ids={})
        for key in candidates:
            if key in by_key:  # exact duplicate of an existing concept
                plan.concept_ids[key] = plan.merged[key] = by_key[key].id
            else:
                plan.concept_ids[key] = uuid.uuid4()

        proposed: list[Edge] = []
        context = list(existing)[: self.settings.existing_concepts_context]
        items = list(candidates.values())
        size = max(1, self.settings.relationship_batch_size)
        # Even batches (no tiny trailing batch); each later batch also sees the concepts
        # of earlier batches so cross-batch relationships are still found.
        per_batch = math.ceil(len(items) / max(1, math.ceil(len(items) / size))) if items else 1
        earlier: list[ExistingConcept] = []
        try:
            for start in range(0, len(items), per_batch):
                batch = items[start : start + per_batch]
                batch_context = context + earlier[-size:]
                if len(batch) + len(batch_context) >= 2:
                    proposed += await self._detect(batch, batch_context, plan, ctx, subject_name)
                earlier += [
                    ExistingConcept(plan.concept_ids[c.key], c.key, c.name, c.description)
                    for c in batch
                ]
        except LLMOutputError as exc:
            # Relationships are best effort; extraction-derived edges still apply.
            logger.warning("relationship detection failed", extra={"error": str(exc)[:200]})
            plan.relationship_detection = "failed"

        proposed += self._edges_from_extraction(candidates, by_key, plan)
        plan.edges = proposed
        return plan

    async def _detect(
        self,
        batch: list[CandidateConcept],
        context: list[ExistingConcept],
        plan: GraphPlan,
        ctx: AICallContext,
        subject_name: str | None,
    ) -> list[Edge]:
        new_refs = [(f"N{i}", c) for i, c in enumerate(batch, start=1)]
        existing_refs = [(f"E{i}", e) for i, e in enumerate(context, start=1)]
        messages, version = self.build_messages(new_refs, existing_refs, subject_name)
        output = await self.llm.complete_json(
            TASK,
            messages,
            ctx.for_purpose(
                "concept_relationships", prompt_name=RELATIONSHIP_PROMPT, prompt_version=version
            ),
            RelationshipOutput,
            coerce=coerce_relationship_output,
        )
        new_by_ref = dict(new_refs)
        existing_by_ref = dict(existing_refs)

        for dup in output.duplicates:
            cand, target = (
                new_by_ref.get(dup.new.strip()),
                existing_by_ref.get(dup.existing.strip()),
            )
            if cand is not None and target is not None and cand.key not in plan.merged:
                plan.concept_ids[cand.key] = plan.merged[cand.key] = target.id

        def resolve(ref: str) -> uuid.UUID | None:
            ref = ref.strip()
            if (cand := new_by_ref.get(ref)) is not None:
                return plan.concept_ids[cand.key]
            if (ex := existing_by_ref.get(ref)) is not None:
                return ex.id
            return None

        edges = []
        for rel in output.relationships:
            src, dst = resolve(rel.source), resolve(rel.target)
            if src is not None and dst is not None:
                edges.append(Edge(src, dst, rel.type, rel.strength))
        return edges

    @staticmethod
    def _edges_from_extraction(
        candidates: dict[str, CandidateConcept],
        existing: dict[str, ExistingConcept],
        plan: GraphPlan,
    ) -> list[Edge]:
        def resolve(key: str) -> uuid.UUID | None:
            if key in plan.concept_ids:
                return plan.concept_ids[key]
            return existing[key].id if key in existing else None

        edges = []
        for cand in candidates.values():
            me = plan.concept_ids[cand.key]
            for pre in cand.prerequisites:
                if (src := resolve(pre)) is not None:
                    edges.append(Edge(src, me, "prerequisite", 0.7))
            for other, kind in cand.related.items():
                if (oid := resolve(other)) is None:
                    continue
                # Extraction gives the OTHER concept's role relative to this one.
                if kind == "generalisation":
                    edges.append(Edge(oid, me, "generalisation", 0.6))
                elif kind == "specialisation":
                    edges.append(Edge(oid, me, "specialisation", 0.6))
                else:
                    edges.append(Edge(me, oid, "related", 0.5))
        return edges
