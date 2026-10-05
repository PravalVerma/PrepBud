"""Concept selection and prerequisite traversal (LEARNING_ENGINE §3)."""

from __future__ import annotations

import uuid

from app.services.learning_engine.concept_selector import (
    ConceptGraph,
    ConceptSelectionInput,
    ReviewCandidate,
    filter_prerequisites_met,
    prerequisites_met,
    select_concepts,
    teachable_frontier,
)

LIMITS, DERIV, CHAIN, POWER, VECTORS, MATRICES = (uuid.uuid4() for _ in range(6))
ALL = [LIMITS, DERIV, CHAIN, POWER, VECTORS, MATRICES]
GRAPH = ConceptGraph.from_edges(
    [(LIMITS, DERIV), (DERIV, CHAIN), (DERIV, POWER), (VECTORS, MATRICES)]
)


def select(**kw: object) -> list[uuid.UUID]:
    params = {"concepts": ALL, "mastery": {}, "graph": GRAPH, "max_concepts": 5} | kw
    return select_concepts(ConceptSelectionInput(**params)).concept_ids  # type: ignore[arg-type]


class TestGraph:
    def test_prerequisites(self) -> None:
        assert GRAPH.get_prerequisites(CHAIN) == {DERIV}
        assert GRAPH.get_prerequisites(LIMITS) == set()
        assert ConceptGraph.from_edges([(LIMITS, LIMITS)]).get_prerequisites(LIMITS) == set()

    def test_prerequisite_check(self) -> None:
        assert prerequisites_met(LIMITS, {}, GRAPH, 0.6)
        assert not prerequisites_met(DERIV, {LIMITS: 0.59}, GRAPH, 0.6)
        assert prerequisites_met(DERIV, {LIMITS: 0.6}, GRAPH, 0.6)
        assert filter_prerequisites_met([DERIV, LIMITS, CHAIN], {LIMITS: 0.7}, GRAPH) == [
            DERIV,
            LIMITS,
        ]

    def test_frontier_walks_to_teachable_prerequisites(self) -> None:
        assert teachable_frontier(CHAIN, {}, GRAPH, 0.6) == [LIMITS]
        assert teachable_frontier(CHAIN, {LIMITS: 0.9}, GRAPH, 0.6) == [DERIV]
        assert teachable_frontier(CHAIN, {LIMITS: 0.9, DERIV: 0.7}, GRAPH, 0.6) == [CHAIN]

    def test_frontier_survives_a_cycle(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        cyclic = ConceptGraph.from_edges([(a, b), (b, a)])
        assert teachable_frontier(a, {}, cyclic, 0.6) == []


class TestSelection:
    def test_weakest_first_with_prerequisites_respected(self) -> None:
        chosen = select(mastery={LIMITS: 0.3, VECTORS: 0.1})
        assert chosen[0] == VECTORS
        assert DERIV not in chosen and CHAIN not in chosen and MATRICES not in chosen
        assert set(chosen) == {VECTORS, LIMITS}

    def test_mastered_concepts_are_not_retaught(self) -> None:
        mastered = dict.fromkeys(ALL, 0.9)
        assert select(mastery=mastered) == []

    def test_overdue_reviews_come_first(self) -> None:
        reviews = [
            ReviewCandidate(POWER, priority=0.9, overdue=False),
            ReviewCandidate(MATRICES, priority=0.1, overdue=True),
        ]
        result = select_concepts(
            ConceptSelectionInput(
                concepts=ALL, mastery={}, graph=GRAPH, due_reviews=reviews, max_concepts=5
            )
        )
        assert result.concept_ids[:2] == [MATRICES, POWER]
        assert result.review_ids == {MATRICES, POWER}
        assert result.reasons[MATRICES] == "overdue_review"

    def test_teach_sessions_skip_reviews(self) -> None:
        chosen = select(session_type="teach", due_reviews=[ReviewCandidate(POWER, overdue=True)])
        assert POWER not in chosen

    def test_goal_concepts_before_others(self) -> None:
        mastery = {LIMITS: 0.9, DERIV: 0.7, VECTORS: 0.0}
        chosen = select(mastery=mastery, goal_concepts=[CHAIN, POWER], max_concepts=2)
        assert set(chosen) == {CHAIN, POWER}

    def test_goal_concept_with_unmet_prerequisite_teaches_prerequisite(self) -> None:
        result = select_concepts(
            ConceptSelectionInput(
                concepts=ALL, mastery={}, graph=GRAPH, goal_concepts=[CHAIN], max_concepts=1
            )
        )
        assert result.concept_ids == [LIMITS]
        assert result.reasons[LIMITS] == "prerequisite"

    def test_explicit_request_is_honoured_prerequisites_first(self) -> None:
        result = select_concepts(
            ConceptSelectionInput(
                concepts=ALL, mastery={LIMITS: 0.9}, graph=GRAPH, requested=[CHAIN], max_concepts=5
            )
        )
        assert result.concept_ids == [DERIV, CHAIN]
        assert result.reasons[CHAIN] == "requested"

    def test_requested_mastered_concept_becomes_review(self) -> None:
        result = select_concepts(
            ConceptSelectionInput(
                concepts=ALL, mastery={VECTORS: 0.95}, graph=GRAPH, requested=[VECTORS]
            )
        )
        assert result.concept_ids == [VECTORS] and result.review_ids == {VECTORS}

    def test_unknown_requested_concepts_ignored(self) -> None:
        assert select(requested=[uuid.uuid4()]) == []

    def test_review_session_only_reviews(self) -> None:
        result = select_concepts(
            ConceptSelectionInput(
                concepts=ALL,
                mastery={LIMITS: 0.5, VECTORS: 0.2},
                graph=GRAPH,
                session_type="review",
                due_reviews=[ReviewCandidate(LIMITS)],
            )
        )
        assert result.concept_ids == [LIMITS]

    def test_review_session_without_due_items_refreshes_learned(self) -> None:
        result = select_concepts(
            ConceptSelectionInput(
                concepts=ALL,
                mastery={LIMITS: 0.5, VECTORS: 0.2},
                graph=GRAPH,
                session_type="review",
            )
        )
        assert result.concept_ids == [VECTORS, LIMITS]
        assert result.review_ids == {VECTORS, LIMITS}
        empty = select_concepts(
            ConceptSelectionInput(concepts=ALL, mastery={}, graph=GRAPH, session_type="review")
        )
        assert empty.concept_ids == []

    def test_deduplicates_and_limits(self) -> None:
        reviews = [ReviewCandidate(LIMITS, overdue=True), ReviewCandidate(LIMITS)]
        chosen = select(due_reviews=reviews, goal_concepts=[LIMITS], max_concepts=2)
        assert chosen.count(LIMITS) == 1 and len(chosen) == 2

    def test_out_of_scope_reviews_ignored(self) -> None:
        assert (
            select(due_reviews=[ReviewCandidate(uuid.uuid4(), overdue=True)], max_concepts=1) != []
        )

    def test_ties_broken_by_difficulty(self) -> None:
        easy, hard = uuid.uuid4(), uuid.uuid4()
        chosen = select_concepts(
            ConceptSelectionInput(
                concepts=[hard, easy],
                mastery={},
                graph=ConceptGraph(),
                difficulty={easy: 0.2, hard: 0.8},
                max_concepts=1,
            )
        ).concept_ids
        assert chosen == [easy]
