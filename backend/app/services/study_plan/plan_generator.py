"""Study plan generation (LEARNING_ENGINE §8.2) — a pure function over plain data.

For every active goal:

1. the goal's concepts are ordered by their prerequisites (topological sort; ties go to
   the easier concept first),
2. concepts already learned (mastery ≥ the learned threshold) are dropped,
3. the rest are spread over the days left until the goal's target date — or
   ``concepts_per_day`` a day without one — starting today; earlier items get a higher
   priority.

Then spaced-repetition reviews are interleaved: every practised concept whose SM-2 review
falls within the planning horizon (overdue ones keep their original date, so they surface
as overdue), and every concept that had been learned (≥ the review trigger) but has since
decayed below it ("forgotten" — reviewed today).

A concept that is both a goal concept and due for review gets one item, on the earlier
date. Concepts already completed or skipped today are moved to tomorrow at the earliest.
"""

from __future__ import annotations

import heapq
import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from app.services.learning_engine.concept_selector import ConceptGraph

ItemKind = Literal["learn", "review"]
ALGORITHM = "goal_topological_sm2_v1"


@dataclass(frozen=True, slots=True)
class PlanConcept:
    id: uuid.UUID
    difficulty: float = 0.5
    mastery: float = 0.0  # effective (decayed) mastery
    assessed_mastery: float = 0.0  # mastery at the last assessment
    attempt_count: int = 0
    next_review: date | None = None


@dataclass(frozen=True, slots=True)
class GoalSpec:
    id: uuid.UUID
    concept_ids: Sequence[uuid.UUID]
    target_date: date | None = None


@dataclass(slots=True)
class PlanInput:
    today: date
    concepts: dict[uuid.UUID, PlanConcept]
    goals: Sequence[GoalSpec] = ()
    graph: ConceptGraph = field(default_factory=ConceptGraph)
    learned_threshold: float = 0.80
    review_trigger: float = 0.70
    concepts_per_day: int = 3
    horizon_days: int = 14
    done_today: frozenset[uuid.UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class PlannedItem:
    concept_id: uuid.UUID
    scheduled_date: date
    priority: float
    kind: ItemKind
    goal_id: uuid.UUID | None = None
    estimated_minutes: int = 15

    def overdue(self, today: date) -> bool:
        return self.scheduled_date < today


@dataclass(slots=True)
class PlanOutput:
    items: list[PlannedItem]
    metadata: dict[str, object]


def topological_order(
    concept_ids: Iterable[uuid.UUID], graph: ConceptGraph, difficulty: dict[uuid.UUID, float]
) -> list[uuid.UUID]:
    """Prerequisites first (Kahn's algorithm over the induced subgraph); easier first on ties.

    Edges outside ``concept_ids`` are ignored; a cycle (never stored, but defensive) is
    broken by appending the remaining concepts in tie-break order.
    """
    ids = list(dict.fromkeys(concept_ids))
    members = set(ids)
    indegree = dict.fromkeys(ids, 0)
    dependents: dict[uuid.UUID, list[uuid.UUID]] = {c: [] for c in ids}
    for c in ids:
        for p in graph.get_prerequisites(c):
            if p in members:
                indegree[c] += 1
                dependents[p].append(c)

    def key(c: uuid.UUID) -> tuple[float, str]:
        return (difficulty.get(c, 0.5), str(c))

    ready = [(key(c), c) for c in ids if indegree[c] == 0]
    heapq.heapify(ready)
    order: list[uuid.UUID] = []
    while ready:
        _, c = heapq.heappop(ready)
        order.append(c)
        for d in dependents[c]:
            indegree[d] -= 1
            if indegree[d] == 0:
                heapq.heappush(ready, (key(d), d))
    if len(order) < len(ids):
        placed = set(order)
        order += sorted((c for c in ids if c not in placed), key=key)
    return order


def estimate_minutes(mastery: float, difficulty: float) -> int:
    """LEARNING_ENGINE §8.2 step 4: 15–45 minutes depending on the gap."""
    return int(15 + 30 * (1 - mastery) * difficulty)


def concepts_per_day_for(count: int, today: date, target: date | None, default: int) -> int:
    """Enough per day to finish by the target date (inclusive); ``default`` without one.

    (The doc's ``len // days`` would overrun the deadline; rounding up finishes on time.)
    """
    if target is None or count == 0:
        return max(1, default)
    days = max(1, (target - today).days + 1)
    return max(1, math.ceil(count / days))


def review_priority(concept: PlanConcept, scheduled: date, today: date) -> float:
    overdue_days = max(0, (today - scheduled).days)
    return round(min(1.0, 0.5 + 0.1 * overdue_days + 0.3 * (1 - concept.mastery)), 3)


def generate_plan(inp: PlanInput) -> PlanOutput:
    today = inp.today
    earliest_for_done = today + timedelta(days=1)
    difficulty = {c.id: c.difficulty for c in inp.concepts.values()}
    items: dict[uuid.UUID, PlannedItem] = {}

    def place(item: PlannedItem) -> None:
        if item.concept_id in inp.done_today and item.scheduled_date < earliest_for_done:
            item = PlannedItem(
                item.concept_id,
                earliest_for_done,
                item.priority,
                item.kind,
                item.goal_id,
                item.estimated_minutes,
            )
        current = items.get(item.concept_id)
        if current is None:
            items[item.concept_id] = item
            return
        # One item per concept: the earlier date, the higher priority; learning wins the kind.
        kind: ItemKind = "learn" if "learn" in (current.kind, item.kind) else "review"
        items[item.concept_id] = PlannedItem(
            item.concept_id,
            min(current.scheduled_date, item.scheduled_date),
            max(current.priority, item.priority),
            kind,
            current.goal_id or item.goal_id,
            max(current.estimated_minutes, item.estimated_minutes),
        )

    # 1–3. Goal concepts to learn, prerequisites first, spread to the target date.
    per_goal: dict[str, int] = {}
    for goal in inp.goals:
        ordered = topological_order(
            (c for c in goal.concept_ids if c in inp.concepts), inp.graph, difficulty
        )
        to_learn = [c for c in ordered if inp.concepts[c].mastery < inp.learned_threshold]
        per_day = concepts_per_day_for(len(to_learn), today, goal.target_date, inp.concepts_per_day)
        per_goal[str(goal.id)] = per_day
        for i, concept_id in enumerate(to_learn):
            concept = inp.concepts[concept_id]
            place(
                PlannedItem(
                    concept_id=concept_id,
                    scheduled_date=today + timedelta(days=i // per_day),
                    priority=round(1.0 - i / len(to_learn), 3),
                    kind="learn",
                    goal_id=goal.id,
                    estimated_minutes=estimate_minutes(concept.mastery, concept.difficulty),
                )
            )

    # 7. Spaced-repetition reviews within the horizon, and forgotten concepts.
    horizon = today + timedelta(days=inp.horizon_days)
    for concept in inp.concepts.values():
        if concept.attempt_count <= 0:
            continue
        scheduled: date | None = None
        if concept.next_review is not None and concept.next_review <= horizon:
            scheduled = concept.next_review
        if concept.assessed_mastery >= inp.review_trigger and concept.mastery < inp.review_trigger:
            scheduled = min(scheduled, today) if scheduled else today
        if scheduled is None:
            continue
        place(
            PlannedItem(
                concept_id=concept.id,
                scheduled_date=scheduled,
                priority=review_priority(concept, scheduled, today),
                kind="review",
                estimated_minutes=10,
            )
        )

    ordered_items = sorted(
        items.values(), key=lambda i: (i.scheduled_date, -i.priority, str(i.concept_id))
    )
    return PlanOutput(
        items=ordered_items,
        metadata={
            "algorithm": ALGORITHM,
            "generated_for": today.isoformat(),
            "goal_ids": [str(g.id) for g in inp.goals],
            "concepts_per_day": per_goal,
            "default_concepts_per_day": inp.concepts_per_day,
            "horizon_days": inp.horizon_days,
            "estimated_minutes": sum(i.estimated_minutes for i in ordered_items),
            "kinds": {str(i.concept_id): i.kind for i in ordered_items},
            "goals_by_concept": {
                str(i.concept_id): str(i.goal_id) for i in ordered_items if i.goal_id
            },
        },
    )
