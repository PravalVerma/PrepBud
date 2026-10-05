"""Concept selection (LEARNING_ENGINE §3): which concepts a session works on.

Priority (deduplicated, capped at ``max_concepts``):

0. concepts the student explicitly asked for (with any unmet prerequisites first),
1. overdue reviews, then reviews due today (not for "teach" sessions),
2. goal concepts, lowest mastery first,
3. the weakest concepts overall.

Concepts to *learn* (2 and 3) must have their prerequisites mastered to at least the
prerequisite threshold. When they don't, the prerequisite graph is walked and the unmet
prerequisites that *are* teachable are selected instead — "weakest prerequisite first".
Mastered concepts are only ever selected as reviews.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

SESSION_TYPES = ("teach", "practice", "review", "mixed")


@dataclass(slots=True)
class ConceptGraph:
    """Prerequisite edges: ``prerequisites[target]`` = concepts to learn before ``target``."""

    prerequisites: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=lambda: defaultdict(set))

    @classmethod
    def from_edges(cls, edges: Iterable[tuple[uuid.UUID, uuid.UUID]]) -> ConceptGraph:
        graph = cls()
        for source, target in edges:
            if source != target:
                graph.prerequisites[target].add(source)
        return graph

    def get_prerequisites(self, concept_id: uuid.UUID) -> set[uuid.UUID]:
        return set(self.prerequisites.get(concept_id, ()))


def prerequisites_met(
    concept_id: uuid.UUID, mastery: dict[uuid.UUID, float], graph: ConceptGraph, threshold: float
) -> bool:
    return all(mastery.get(p, 0.0) >= threshold for p in graph.get_prerequisites(concept_id))


def filter_prerequisites_met(
    concepts: Sequence[uuid.UUID],
    mastery: dict[uuid.UUID, float],
    graph: ConceptGraph,
    threshold: float = 0.6,
) -> list[uuid.UUID]:
    return [c for c in concepts if prerequisites_met(c, mastery, graph, threshold)]


def teachable_frontier(
    concept_id: uuid.UUID,
    mastery: dict[uuid.UUID, float],
    graph: ConceptGraph,
    threshold: float,
    _visiting: frozenset[uuid.UUID] = frozenset(),
) -> list[uuid.UUID]:
    """What to teach on the way to ``concept_id``: the concept itself if its prerequisites
    are met, otherwise the teachable unmet prerequisites (weakest first, recursively)."""
    if concept_id in _visiting:  # defensive: the graph is acyclic by construction
        return []
    unmet = sorted(
        (p for p in graph.get_prerequisites(concept_id) if mastery.get(p, 0.0) < threshold),
        key=lambda p: (mastery.get(p, 0.0), str(p)),
    )
    if not unmet:
        return [concept_id]
    out: list[uuid.UUID] = []
    for prereq in unmet:
        for c in teachable_frontier(prereq, mastery, graph, threshold, _visiting | {concept_id}):
            if c not in out:
                out.append(c)
    return out


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    concept_id: uuid.UUID
    priority: float = 0.5
    overdue: bool = False


@dataclass(slots=True)
class ConceptSelectionInput:
    concepts: Sequence[uuid.UUID]  # every concept in scope
    mastery: dict[uuid.UUID, float]
    graph: ConceptGraph
    session_type: str = "mixed"
    max_concepts: int = 5
    due_reviews: Sequence[ReviewCandidate] = ()
    goal_concepts: Sequence[uuid.UUID] | None = None
    requested: Sequence[uuid.UUID] | None = None
    difficulty: dict[uuid.UUID, float] = field(default_factory=dict)
    prerequisite_threshold: float = 0.6
    learned_threshold: float = 0.8


@dataclass(slots=True)
class ConceptSelection:
    concept_ids: list[uuid.UUID]
    review_ids: set[uuid.UUID]
    reasons: dict[uuid.UUID, str]


def select_concepts(inp: ConceptSelectionInput) -> ConceptSelection:
    chosen: list[uuid.UUID] = []
    reasons: dict[uuid.UUID, str] = {}
    reviews: set[uuid.UUID] = set()
    in_scope = set(inp.concepts)

    def full() -> bool:
        return len(chosen) >= inp.max_concepts

    def add(concept_id: uuid.UUID, reason: str, *, review: bool = False) -> None:
        if full() or concept_id in reasons:
            return
        chosen.append(concept_id)
        reasons[concept_id] = reason
        if review:
            reviews.add(concept_id)

    def add_learnable(concept_id: uuid.UUID, reason: str) -> None:
        if inp.mastery.get(concept_id, 0.0) >= inp.learned_threshold:
            return
        for c in teachable_frontier(concept_id, inp.mastery, inp.graph, inp.prerequisite_threshold):
            add(c, reason if c == concept_id else "prerequisite")

    def mastery_key(c: uuid.UUID) -> tuple[float, float, str]:
        return (inp.mastery.get(c, 0.0), inp.difficulty.get(c, 0.5), str(c))

    # 0. Student's explicit choice — honoured even if already mastered.
    for concept_id in inp.requested or ():
        if concept_id not in in_scope:
            continue
        frontier = teachable_frontier(
            concept_id, inp.mastery, inp.graph, inp.prerequisite_threshold
        )
        for c in frontier:
            if c != concept_id:
                add(c, "prerequisite")
        add(
            concept_id,
            "requested",
            review=inp.mastery.get(concept_id, 0.0) >= inp.learned_threshold,
        )
    if inp.requested:
        return ConceptSelection(chosen, reviews, reasons)

    # 1. Spaced-repetition obligations.
    if inp.session_type != "teach":
        ordered = sorted(
            inp.due_reviews, key=lambda r: (not r.overdue, -r.priority, str(r.concept_id))
        )
        for r in ordered:
            if r.concept_id in in_scope:
                add(r.concept_id, "overdue_review" if r.overdue else "due_review", review=True)
    if inp.session_type == "review":
        if not chosen:  # nothing due: revisit what was partly learned, weakest first
            learned = sorted(
                (c for c in inp.concepts if inp.mastery.get(c, 0.0) > 0), key=mastery_key
            )
            for c in learned:
                add(c, "refresh", review=True)
        return ConceptSelection(chosen, reviews, reasons)

    # 2. Goal-driven concepts.
    for c in sorted((c for c in inp.goal_concepts or () if c in in_scope), key=mastery_key):
        add_learnable(c, "goal")

    # 3. Weakest *teachable* concepts overall: collect every concept on the path to an
    #    unmastered concept, then order them by their own mastery.
    teachable: dict[uuid.UUID, str] = {}
    for c in sorted(inp.concepts, key=mastery_key):
        if inp.mastery.get(c, 0.0) >= inp.learned_threshold:
            continue
        for t in teachable_frontier(c, inp.mastery, inp.graph, inp.prerequisite_threshold):
            teachable.setdefault(t, "weakest" if t == c else "prerequisite")
    for c in sorted(teachable, key=mastery_key):
        add(c, teachable[c])

    return ConceptSelection(chosen, reviews, reasons)
