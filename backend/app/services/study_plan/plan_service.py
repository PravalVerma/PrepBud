"""Study plans in the database (LEARNING_ENGINE §8, API_CONTRACT §3.7, §3.10).

One **active** plan per user covers all of their active goals plus spaced-repetition
reviews. Regenerating supersedes it (``status = 'superseded'``) and writes a new one, so
plans are an append-only history; today's completed and skipped items are carried over so
the day's progress stays visible. Regeneration is serialised per user with a transaction
advisory lock and is deterministic for a given day — safe to run twice (Celery retries,
concurrent triggers).

Triggers (LEARNING_ENGINE §8.3): goal created/changed/deleted, session completed, a new
document processed, the daily maintenance job, and lazily on read when the plan was made
on an earlier day.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import (
    Chapter,
    Concept,
    ConceptRelationship,
    Course,
    Document,
    DocumentSection,
    DocumentSectionConcept,
    LearningGoal,
    ReviewItem,
    StudyPlan,
)
from app.domain.common import mastery_label
from app.services.learning_engine.concept_selector import ConceptGraph
from app.services.student_model.mastery_tracker import MasterySnapshot, MasteryTracker
from app.services.study_plan.plan_generator import (
    GoalSpec,
    PlanConcept,
    PlanInput,
    generate_plan,
)

logger = get_logger(__name__)

OPEN_STATUSES = ("pending", "overdue")
CARRIED_STATUSES = ("completed", "skipped")


@dataclass(frozen=True, slots=True)
class GoalProgress:
    concept_count: int
    mastered_count: int
    average_mastery: float
    progress: float  # 0–1: mean of min(mastery / learned threshold, 1)
    days_remaining: int | None
    all_mastered: bool


async def goal_concept_ids(
    session: AsyncSession, user_id: uuid.UUID, goal: LearningGoal
) -> list[uuid.UUID]:
    """The concepts a goal covers: its explicit list, else its course, else its subject,
    else everything the student has."""
    owned = select(Concept.id).where(Concept.user_id == user_id)
    if goal.target_concepts:
        ids = set((await session.scalars(owned.where(Concept.id.in_(goal.target_concepts)))).all())
        return [c for c in dict.fromkeys(goal.target_concepts) if c in ids]
    if goal.course_id:
        chapters = select(Chapter.id).where(Chapter.course_id == goal.course_id)
        from_documents = (
            select(DocumentSectionConcept.concept_id)
            .join(DocumentSection, DocumentSection.id == DocumentSectionConcept.document_section_id)
            .join(Document, Document.id == DocumentSection.document_id)
            .where(Document.course_id == goal.course_id, Document.user_id == user_id)
        )
        stmt = owned.where(or_(Concept.chapter_id.in_(chapters), Concept.id.in_(from_documents)))
    elif goal.subject_id:
        chapters = (
            select(Chapter.id)
            .join(Course, Course.id == Chapter.course_id)
            .where(Course.subject_id == goal.subject_id)
        )
        stmt = owned.where(
            or_(Concept.subject_id == goal.subject_id, Concept.chapter_id.in_(chapters))
        )
    else:
        stmt = owned
    return list((await session.scalars(stmt.order_by(Concept.created_at, Concept.id))).all())


class StudyPlanService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        redis: Redis | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.settings = settings
        self.cfg = settings.learning_engine
        self.now = now
        self.tracker = MasteryTracker(session, self.cfg, redis=redis, now=now)

    def today(self) -> date:
        return self.now().date()

    # ---- reads ----------------------------------------------------------------------------

    async def active_plan(
        self, user_id: uuid.UUID, *, for_update: bool = False
    ) -> StudyPlan | None:
        stmt = (
            select(StudyPlan)
            .where(StudyPlan.user_id == user_id, StudyPlan.status == "active")
            .order_by(StudyPlan.generated_at.desc())
            .limit(1)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def items(self, plan_id: uuid.UUID) -> Sequence[ReviewItem]:
        return (
            await self.session.scalars(
                select(ReviewItem).where(ReviewItem.study_plan_id == plan_id)
            )
        ).all()

    async def active_goals(self, user_id: uuid.UUID) -> Sequence[LearningGoal]:
        return (
            await self.session.scalars(
                select(LearningGoal)
                .where(LearningGoal.user_id == user_id, LearningGoal.status == "active")
                .order_by(LearningGoal.created_at, LearningGoal.id)
            )
        ).all()

    async def ensure_plan(self, user_id: uuid.UUID) -> StudyPlan:
        """The active plan, (re)generated if there is none or it was made on an earlier day."""
        plan = await self.active_plan(user_id)
        made_for = ((plan.generation_metadata or {}).get("generated_for") if plan else None) or ""
        if plan is None or made_for < self.today().isoformat():
            plan = await self.regenerate(user_id, reason="refresh" if plan else "initial")
        return plan

    # ---- generation -----------------------------------------------------------------------

    async def _lock(self, user_id: uuid.UUID) -> None:
        await self.session.execute(
            select(func.pg_advisory_xact_lock(func.hashtext(f"study_plan:{user_id}")))
        )

    async def regenerate(self, user_id: uuid.UUID, *, reason: str) -> StudyPlan:
        """Supersede the active plan with a fresh one (caller commits)."""
        await self._lock(user_id)
        today = self.today()
        previous = await self.active_plan(user_id, for_update=True)
        carried: list[ReviewItem] = []
        previous_kinds: dict[str, str] = {}
        previous_goals: dict[str, str] = {}
        if previous is not None:
            meta = previous.generation_metadata or {}
            previous_kinds = dict(meta.get("item_kinds") or {})
            previous_goals = dict(meta.get("item_goals") or {})
            for item in await self.items(previous.id):
                when = item.completed_at or item.updated_at
                if item.status in CARRIED_STATUSES and when is not None and when.date() == today:
                    carried.append(item)

        concepts = (
            await self.session.execute(
                select(Concept.id, Concept.difficulty_estimate).where(Concept.user_id == user_id)
            )
        ).all()
        ids = [c.id for c in concepts]
        snapshots = await self.tracker.snapshot(user_id, ids) if ids else {}
        plan_concepts = {
            c.id: _plan_concept(c.id, float(c.difficulty_estimate or 0.5), snapshots.get(c.id))
            for c in concepts
        }
        edges = (
            await self.session.execute(
                select(ConceptRelationship.source_concept_id, ConceptRelationship.target_concept_id)
                .join(Concept, Concept.id == ConceptRelationship.target_concept_id)
                .where(
                    Concept.user_id == user_id,
                    ConceptRelationship.relationship_type == "prerequisite",
                )
            )
        ).all()
        goals = await self.active_goals(user_id)
        specs = [
            GoalSpec(g.id, await goal_concept_ids(self.session, user_id, g), g.target_date)
            for g in goals
        ]
        output = generate_plan(
            PlanInput(
                today=today,
                concepts=plan_concepts,
                goals=specs,
                graph=ConceptGraph.from_edges((e[0], e[1]) for e in edges),
                learned_threshold=self.cfg.mastery_learned_threshold,
                review_trigger=self.cfg.mastery_review_trigger,
                concepts_per_day=self.cfg.default_concepts_per_day,
                horizon_days=self.cfg.study_plan_horizon_days,
                done_today=frozenset(i.concept_id for i in carried),
            )
        )

        if previous is not None:
            previous.status = "superseded"
        now = self.now()
        plan = StudyPlan(
            user_id=user_id,
            learning_goal_id=goals[0].id if len(goals) == 1 else None,
            status="active",
            generated_at=now,
            valid_until=now + timedelta(days=self.cfg.study_plan_horizon_days),
        )
        self.session.add(plan)
        await self.session.flush()

        kinds: dict[str, str] = {}
        item_goals: dict[str, str] = {}
        rows: list[tuple[ReviewItem, str, str | None]] = []
        for old in carried:
            copy = ReviewItem(
                study_plan_id=plan.id,
                concept_id=old.concept_id,
                scheduled_date=old.scheduled_date,
                priority=old.priority,
                status=old.status,
                completed_at=old.completed_at,
            )
            rows.append(
                (copy, previous_kinds.get(str(old.id), "learn"), previous_goals.get(str(old.id)))
            )
        for planned in output.items:
            item = ReviewItem(
                study_plan_id=plan.id,
                concept_id=planned.concept_id,
                scheduled_date=planned.scheduled_date,
                priority=planned.priority,
                status="overdue" if planned.overdue(today) else "pending",
            )
            rows.append((item, planned.kind, str(planned.goal_id) if planned.goal_id else None))
        self.session.add_all([r[0] for r in rows])
        await self.session.flush()
        for item, kind, goal in rows:
            kinds[str(item.id)] = kind
            if goal:
                item_goals[str(item.id)] = goal
        metadata: dict[str, Any] = {
            k: v for k, v in output.metadata.items() if k not in ("kinds", "goals_by_concept")
        }
        plan.generation_metadata = {
            **metadata,
            "reason": reason,
            "carried_items": len(carried),
            "item_kinds": kinds,
            "item_goals": item_goals,
        }
        await self.session.flush()
        logger.info(
            "study plan generated",
            extra={"user_id": str(user_id), "items": len(rows), "reason": reason},
        )
        return plan

    # ---- events ---------------------------------------------------------------------------

    async def on_session_completed(
        self,
        user_id: uuid.UUID,
        *,
        covered: Iterable[uuid.UUID],
        attempted: Iterable[uuid.UUID],
    ) -> int:
        """Mark today's (and overdue) items done for what the session worked on, then
        regenerate from the new mastery and SM-2 dates (caller commits). Returns the number
        of items completed."""
        await self._lock(user_id)
        covered_ids, attempted_ids = set(covered), set(attempted)
        plan = await self.active_plan(user_id, for_update=True)
        done = 0
        if plan is not None and (covered_ids or attempted_ids):
            kinds = (plan.generation_metadata or {}).get("item_kinds") or {}
            for item in await self.items(plan.id):
                if item.status not in OPEN_STATUSES or item.scheduled_date > self.today():
                    continue
                needed = attempted_ids if kinds.get(str(item.id)) == "review" else covered_ids
                if item.concept_id in needed:
                    item.status = "completed"
                    item.completed_at = self.now()
                    done += 1
            await self.session.flush()
        await self.regenerate(user_id, reason="session_completed")
        return done

    async def mark_overdue(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(ReviewItem)
            .where(
                ReviewItem.study_plan_id.in_(
                    select(StudyPlan.id).where(
                        StudyPlan.user_id == user_id, StudyPlan.status == "active"
                    )
                ),
                ReviewItem.status == "pending",
                ReviewItem.scheduled_date < self.today(),
            )
            .values(status="overdue", updated_at=func.now())
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def update_item(
        self,
        user_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        status: str | None = None,
        scheduled_date: date | None = None,
    ) -> tuple[ReviewItem, StudyPlan]:
        """Skip, complete, un-skip or reschedule an item of the user's active plan."""
        row = (
            await self.session.execute(
                select(ReviewItem, StudyPlan)
                .join(StudyPlan, StudyPlan.id == ReviewItem.study_plan_id)
                .where(
                    ReviewItem.id == item_id,
                    StudyPlan.user_id == user_id,
                    StudyPlan.status == "active",
                )
                .with_for_update(of=ReviewItem)
            )
        ).first()
        if row is None:
            raise NotFoundError("Review item not found")
        item, plan = row
        today = self.today()
        if scheduled_date is not None:
            if scheduled_date < today:
                raise ValidationError(
                    "A review can only be moved to today or later",
                    details={"field": "scheduled_date"},
                )
            item.scheduled_date = scheduled_date
            if status is None and item.status in ("overdue", "skipped"):
                item.status = "pending"
        if status is not None:
            if status == "pending" and item.scheduled_date < today:
                item.status = "overdue"
            else:
                item.status = status
            item.completed_at = self.now() if status == "completed" else None
        item.updated_at = self.now()
        await self.session.flush()
        return item, plan

    # ---- views ----------------------------------------------------------------------------

    async def view(self, user_id: uuid.UUID, plan: StudyPlan) -> dict[str, Any]:
        today = self.today()
        rows = (
            await self.session.execute(
                select(ReviewItem, Concept.name)
                .join(Concept, Concept.id == ReviewItem.concept_id)
                .where(ReviewItem.study_plan_id == plan.id)
            )
        ).all()
        snapshots = await self.tracker.snapshot(user_id, [r[0].concept_id for r in rows])
        meta = plan.generation_metadata or {}
        kinds = meta.get("item_kinds") or {}
        item_goals = meta.get("item_goals") or {}

        def status_of(item: ReviewItem) -> str:
            if item.status == "pending" and item.scheduled_date < today:
                return "overdue"
            return item.status or "pending"

        items = []
        for item, name in rows:
            snap = snapshots.get(item.concept_id)
            level = snap.level if snap else 0.0
            items.append(
                {
                    "id": item.id,
                    "concept_id": item.concept_id,
                    "concept_name": name,
                    "scheduled_date": item.scheduled_date,
                    "priority": round(float(item.priority or 0.0), 3),
                    "status": status_of(item),
                    "kind": kinds.get(str(item.id), "learn"),
                    "goal_id": item_goals.get(str(item.id)),
                    "mastery_level": round(level, 4),
                    "mastery_label": mastery_label(level),
                    "next_review_at": snap.next_review_at if snap else None,
                    "completed_at": item.completed_at,
                }
            )
        group = {"overdue": 0, "pending": 1, "completed": 2, "skipped": 2}
        # AC-6.2: overdue first (oldest first), then by date and priority; done items last.
        items.sort(
            key=lambda i: (
                group.get(i["status"], 1),
                i["scheduled_date"],
                -i["priority"],
                str(i["concept_id"]),
            )
        )
        open_items = [i for i in items if i["status"] in OPEN_STATUSES]
        overdue = sum(1 for i in open_items if i["status"] == "overdue")
        today_count = sum(
            1 for i in open_items if i["status"] == "pending" and i["scheduled_date"] == today
        )
        future = sorted(i["scheduled_date"] for i in open_items if i["scheduled_date"] > today)
        reviews_due = sum(
            1 for i in open_items if i["kind"] == "review" and i["scheduled_date"] <= today
        )
        return {
            "id": plan.id,
            "learning_goal_id": plan.learning_goal_id,
            "status": plan.status,
            "generated_at": plan.generated_at,
            "valid_until": plan.valid_until,
            "items": items,
            "stats": {
                "total_items": len(items),
                "completed": sum(1 for i in items if i["status"] == "completed"),
                "skipped": sum(1 for i in items if i["status"] == "skipped"),
                "overdue": overdue,
                "upcoming_today": today_count,
                "due_today": overdue + today_count,
                "reviews_due": reviews_due,
                "next_due_date": future[0] if future else None,
                "estimated_minutes": meta.get("estimated_minutes", 0),
            },
        }

    async def goal_progress(
        self, user_id: uuid.UUID, goals: Sequence[LearningGoal]
    ) -> dict[uuid.UUID, GoalProgress]:
        scopes = {g.id: await goal_concept_ids(self.session, user_id, g) for g in goals}
        all_ids = {c for ids in scopes.values() for c in ids}
        levels = await self.tracker.levels(user_id, all_ids) if all_ids else {}
        threshold = self.cfg.mastery_learned_threshold
        today = self.today()
        out: dict[uuid.UUID, GoalProgress] = {}
        for goal in goals:
            ids = scopes[goal.id]
            values = [levels.get(c, 0.0) for c in ids]
            mastered = sum(1 for v in values if v >= threshold)
            out[goal.id] = GoalProgress(
                concept_count=len(ids),
                mastered_count=mastered,
                average_mastery=round(sum(values) / len(values), 4) if values else 0.0,
                progress=round(sum(min(v / threshold, 1.0) for v in values) / len(values), 4)
                if values
                else 0.0,
                days_remaining=(goal.target_date - today).days if goal.target_date else None,
                all_mastered=bool(ids) and mastered == len(ids),
            )
        return out


def _plan_concept(
    concept_id: uuid.UUID, difficulty: float, snap: MasterySnapshot | None
) -> PlanConcept:
    if snap is None:
        return PlanConcept(concept_id, difficulty)
    return PlanConcept(
        id=concept_id,
        difficulty=difficulty,
        mastery=snap.level,
        assessed_mastery=snap.assessed_level,
        attempt_count=snap.attempt_count,
        next_review=snap.next_review_at.date() if snap.next_review_at else None,
    )
