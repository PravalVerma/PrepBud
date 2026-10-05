"""Study plan generation (LEARNING_ENGINE §8.2) — pure algorithm."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.services.learning_engine.concept_selector import ConceptGraph
from app.services.study_plan.plan_generator import (
    GoalSpec,
    PlanConcept,
    PlanInput,
    concepts_per_day_for,
    estimate_minutes,
    generate_plan,
    review_priority,
    topological_order,
)

TODAY = date(2026, 10, 5)
A, B, C, D, E = (uuid.UUID(int=i) for i in range(1, 6))


def concepts(**over: PlanConcept) -> dict[uuid.UUID, PlanConcept]:
    base = {c: PlanConcept(c, difficulty=0.5) for c in (A, B, C, D, E)}
    base.update({v.id: v for v in over.values()})
    return base


def by_concept(plan_items: list) -> dict[uuid.UUID, object]:
    return {i.concept_id: i for i in plan_items}


def test_topological_order_prerequisites_first_easier_first() -> None:
    graph = ConceptGraph.from_edges([(A, C), (B, C), (C, D)])  # A, B before C before D
    difficulty = {A: 0.9, B: 0.2, C: 0.1, D: 0.1, E: 0.5}
    order = topological_order([D, C, B, A, E], graph, difficulty)
    assert order.index(A) < order.index(C) < order.index(D)
    assert order.index(B) < order.index(C)
    assert order[0] == B  # easiest concept with no unmet prerequisites
    # Edges to concepts outside the set are ignored.
    assert topological_order([D], graph, difficulty) == [D]


def test_topological_order_survives_a_cycle() -> None:
    graph = ConceptGraph.from_edges([(A, B), (B, A), (C, A)])
    order = topological_order([A, B, C], graph, {})
    assert sorted(order) == sorted([A, B, C]) and order[0] == C


def test_concepts_per_day() -> None:
    assert concepts_per_day_for(7, TODAY, None, 3) == 3
    assert concepts_per_day_for(7, TODAY, TODAY + timedelta(days=2), 3) == 3  # ceil(7/3)
    assert concepts_per_day_for(10, TODAY, TODAY + timedelta(days=1), 3) == 5
    assert concepts_per_day_for(10, TODAY, TODAY - timedelta(days=4), 3) == 10  # overdue goal
    assert concepts_per_day_for(0, TODAY, None, 0) == 1


def test_estimates_and_review_priority() -> None:
    assert estimate_minutes(0.0, 1.0) == 45 and estimate_minutes(1.0, 1.0) == 15
    c = PlanConcept(A, mastery=0.5)
    assert review_priority(c, TODAY, TODAY) == 0.65
    assert review_priority(c, TODAY - timedelta(days=3), TODAY) == 0.95
    assert review_priority(c, TODAY - timedelta(days=30), TODAY) == 1.0


def test_goal_concepts_spread_in_prerequisite_order() -> None:
    graph = ConceptGraph.from_edges([(A, B), (B, C), (C, D), (D, E)])
    plan = generate_plan(
        PlanInput(
            today=TODAY,
            concepts=concepts(),
            goals=[GoalSpec(uuid.uuid4(), [E, D, C, B, A])],
            graph=graph,
            concepts_per_day=2,
        )
    )
    assert [i.concept_id for i in plan.items] == [A, B, C, D, E]
    assert [i.scheduled_date for i in plan.items] == [
        TODAY,
        TODAY,
        TODAY + timedelta(days=1),
        TODAY + timedelta(days=1),
        TODAY + timedelta(days=2),
    ]
    priorities = [i.priority for i in plan.items]
    assert priorities == sorted(priorities, reverse=True) and priorities[0] == 1.0
    assert {i.kind for i in plan.items} == {"learn"}
    assert plan.metadata["algorithm"] and plan.metadata["estimated_minutes"] > 0


def test_mastered_concepts_are_skipped_and_deadline_compresses() -> None:
    goal = GoalSpec(uuid.uuid4(), [A, B, C, D], target_date=TODAY + timedelta(days=1))
    plan = generate_plan(
        PlanInput(today=TODAY, concepts=concepts(a=PlanConcept(A, mastery=0.85)), goals=[goal])
    )
    items = by_concept(plan.items)
    assert A not in items
    # 3 concepts over 2 days (today + tomorrow) → 2 per day.
    assert sorted(i.scheduled_date for i in plan.items) == [TODAY, TODAY, TODAY + timedelta(1)]
    assert plan.metadata["concepts_per_day"] == {str(goal.id): 2}


def test_goals_merge_one_item_per_concept() -> None:
    g1, g2 = GoalSpec(uuid.uuid4(), [A, B, C, D]), GoalSpec(uuid.uuid4(), [D])
    plan = generate_plan(
        PlanInput(today=TODAY, concepts=concepts(), goals=[g1, g2], concepts_per_day=3)
    )
    items = by_concept(plan.items)
    assert len(plan.items) == 4
    assert items[D].scheduled_date == TODAY  # earlier date wins (g2 schedules it first)
    assert items[D].priority == 1.0 and items[D].goal_id == g1.id


def test_reviews_due_overdue_forgotten_and_horizon() -> None:
    plan = generate_plan(
        PlanInput(
            today=TODAY,
            horizon_days=7,
            concepts=concepts(
                a=PlanConcept(A, mastery=0.75, attempt_count=3, next_review=TODAY - timedelta(2)),
                b=PlanConcept(B, mastery=0.75, attempt_count=3, next_review=TODAY + timedelta(3)),
                c=PlanConcept(C, mastery=0.75, attempt_count=3, next_review=TODAY + timedelta(30)),
                # learned to 0.9, decayed to 0.5 → forgotten → review today
                d=PlanConcept(D, mastery=0.5, assessed_mastery=0.9, attempt_count=4),
                # never practised: no review even if a date sneaks in
                e=PlanConcept(E, next_review=TODAY),
            ),
        )
    )
    items = by_concept(plan.items)
    assert set(items) == {A, B, D}
    assert items[A].scheduled_date == TODAY - timedelta(2) and items[A].overdue(TODAY)
    assert items[A].priority > items[B].priority
    assert items[D].scheduled_date == TODAY and items[D].kind == "review"
    assert plan.items[0].concept_id == A  # oldest first


def test_learning_and_review_merge_and_done_today_moves_to_tomorrow() -> None:
    plan = generate_plan(
        PlanInput(
            today=TODAY,
            concepts=concepts(
                a=PlanConcept(A, mastery=0.3, attempt_count=2, next_review=TODAY - timedelta(1)),
            ),
            goals=[GoalSpec(uuid.uuid4(), [A, B])],
            done_today=frozenset({B}),
        )
    )
    items = by_concept(plan.items)
    assert items[A].kind == "learn" and items[A].scheduled_date == TODAY - timedelta(1)
    assert items[B].scheduled_date == TODAY + timedelta(1)
    assert plan.metadata["kinds"] == {str(A): "learn", str(B): "learn"}


def test_empty_input() -> None:
    plan = generate_plan(PlanInput(today=TODAY, concepts={}))
    assert plan.items == [] and plan.metadata["estimated_minutes"] == 0
