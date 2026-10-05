"""Dashboard aggregates (API_CONTRACT §3.11–3.13) — read-only, every query user-scoped.

Days are bucketed in the student's own timezone (``student_profiles.timezone``), so "today",
streaks and the activity calendar match what the student sees on their clock.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AIInteraction,
    Concept,
    LearningSession,
    Misconception,
    Question,
    QuestionAttempt,
    StudentConceptMastery,
    StudentMisconception,
    StudentProfile,
    Subject,
)
from app.db.repositories.base import Page, PageRequest


@dataclass(frozen=True, slots=True)
class SubjectMastery:
    id: uuid.UUID | None
    name: str
    concept_count: int
    avg_mastery: float
    mastered_count: int
    struggling_count: int


@dataclass(frozen=True, slots=True)
class DayActivity:
    date: date
    sessions: int = 0
    minutes: float = 0.0
    questions: int = 0
    concepts_practiced: int = 0


@dataclass(frozen=True, slots=True)
class HeatmapRow:
    id: uuid.UUID
    name: str
    subject_id: uuid.UUID | None
    subject_name: str | None
    mastery_level: float
    attempt_count: int
    last_assessed_at: datetime | None
    next_review_at: datetime | None
    history: list[Any]


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def timezone(self, user_id: uuid.UUID) -> str:
        tz = await self.session.scalar(
            select(StudentProfile.timezone).where(StudentProfile.user_id == user_id)
        )
        return tz or "UTC"

    # ---- mastery ------------------------------------------------------------------------

    async def subject_mastery(
        self, user_id: uuid.UUID, *, learned: float, struggling_below: float
    ) -> list[SubjectMastery]:
        level = func.coalesce(StudentConceptMastery.mastery_level, 0.0)
        rows = (
            await self.session.execute(
                select(
                    Concept.subject_id,
                    func.count(Concept.id),
                    func.avg(level),
                    func.count().filter(level >= learned),
                    func.count().filter(
                        StudentConceptMastery.attempt_count > 0, level < struggling_below
                    ),
                )
                .select_from(Concept)
                .outerjoin(
                    StudentConceptMastery,
                    (StudentConceptMastery.concept_id == Concept.id)
                    & (StudentConceptMastery.user_id == user_id),
                )
                .where(Concept.user_id == user_id)
                .group_by(Concept.subject_id)
            )
        ).all()
        stats = {r[0]: r for r in rows}
        subjects = (
            await self.session.execute(
                select(Subject.id, Subject.name)
                .where(Subject.user_id == user_id)
                .order_by(Subject.name)
            )
        ).all()
        out = []
        for subject_id, name in [*subjects, *([(None, "Unsorted")] if None in stats else [])]:
            r = stats.get(subject_id)
            out.append(
                SubjectMastery(
                    id=subject_id,
                    name=name,
                    concept_count=int(r[1]) if r else 0,
                    avg_mastery=round(float(r[2] or 0.0), 4) if r else 0.0,
                    mastered_count=int(r[3]) if r else 0,
                    struggling_count=int(r[4]) if r else 0,
                )
            )
        return out

    async def mastery_distribution(self, user_id: uuid.UUID) -> list[float]:
        """Every concept's mastery (0.0 when never assessed)."""
        level = func.coalesce(StudentConceptMastery.mastery_level, 0.0)
        return [
            float(v)
            for v in (
                await self.session.scalars(
                    select(level)
                    .select_from(Concept)
                    .outerjoin(
                        StudentConceptMastery,
                        (StudentConceptMastery.concept_id == Concept.id)
                        & (StudentConceptMastery.user_id == user_id),
                    )
                    .where(Concept.user_id == user_id)
                )
            ).all()
        ]

    async def heatmap(
        self, user_id: uuid.UUID, *, subject_id: uuid.UUID | None, limit: int
    ) -> tuple[list[HeatmapRow], int]:
        filters = [Concept.user_id == user_id]
        if subject_id is not None:
            filters.append(Concept.subject_id == subject_id)
        total = int(await self.session.scalar(select(func.count(Concept.id)).where(*filters)) or 0)
        rows = (
            await self.session.execute(
                select(
                    Concept.id,
                    Concept.name,
                    Concept.subject_id,
                    Subject.name,
                    StudentConceptMastery.mastery_level,
                    StudentConceptMastery.attempt_count,
                    StudentConceptMastery.last_assessed_at,
                    StudentConceptMastery.next_review_at,
                    StudentConceptMastery.history,
                )
                .select_from(Concept)
                .outerjoin(Subject, Subject.id == Concept.subject_id)
                .outerjoin(
                    StudentConceptMastery,
                    (StudentConceptMastery.concept_id == Concept.id)
                    & (StudentConceptMastery.user_id == user_id),
                )
                .where(*filters)
                # Practised concepts first (most recent activity), then the rest by name.
                .order_by(
                    StudentConceptMastery.last_assessed_at.desc().nulls_last(),
                    Concept.name,
                    Concept.id,
                )
                .limit(limit)
            )
        ).all()
        return [
            HeatmapRow(
                id=r[0],
                name=r[1],
                subject_id=r[2],
                subject_name=r[3],
                mastery_level=float(r[4] or 0.0),
                attempt_count=int(r[5] or 0),
                last_assessed_at=r[6],
                next_review_at=r[7],
                history=list(r[8] or []),
            )
            for r in rows
        ], total

    # ---- activity -----------------------------------------------------------------------

    def _local_day(self, column: Any, tz: str) -> Any:
        return cast(func.timezone(tz, column), Date)

    async def daily_activity(
        self, user_id: uuid.UUID, tz: str, since: datetime
    ) -> dict[date, DayActivity]:
        session_day = self._local_day(LearningSession.started_at, tz)
        sessions = (
            await self.session.execute(
                select(
                    session_day,
                    func.count(LearningSession.id),
                    func.coalesce(func.sum(LearningSession.duration_seconds), 0),
                )
                .where(
                    LearningSession.user_id == user_id,
                    LearningSession.started_at >= since,
                    LearningSession.status.in_(("active", "paused", "completed")),
                )
                .group_by(session_day)
            )
        ).all()
        attempt_day = self._local_day(QuestionAttempt.attempted_at, tz)
        attempts = (
            await self.session.execute(
                select(
                    attempt_day,
                    func.count(QuestionAttempt.id),
                    func.count(func.distinct(Question.concept_id)),
                )
                .join(Question, Question.id == QuestionAttempt.question_id)
                .where(QuestionAttempt.user_id == user_id, QuestionAttempt.attempted_at >= since)
                .group_by(attempt_day)
            )
        ).all()
        days: dict[date, DayActivity] = {}
        for day, count, seconds in sessions:
            days[day] = DayActivity(day, sessions=int(count), minutes=round(float(seconds) / 60, 1))
        for day, questions, concepts in attempts:
            current = days.get(day, DayActivity(day))
            days[day] = DayActivity(
                day,
                sessions=current.sessions,
                minutes=current.minutes,
                questions=int(questions),
                concepts_practiced=int(concepts),
            )
        return days

    async def active_days(self, user_id: uuid.UUID, tz: str, since: datetime) -> set[date]:
        """Days with any learning activity (a session or an answered question)."""
        session_day = self._local_day(LearningSession.started_at, tz)
        attempt_day = self._local_day(QuestionAttempt.attempted_at, tz)
        a = await self.session.scalars(
            select(session_day)
            .where(
                LearningSession.user_id == user_id,
                LearningSession.started_at >= since,
                LearningSession.interaction_count > 0,
            )
            .distinct()
        )
        b = await self.session.scalars(
            select(attempt_day)
            .where(QuestionAttempt.user_id == user_id, QuestionAttempt.attempted_at >= since)
            .distinct()
        )
        return set(a.all()) | set(b.all())

    async def totals(self, user_id: uuid.UUID) -> dict[str, Any]:
        sessions = (
            await self.session.execute(
                select(
                    func.count(LearningSession.id).filter(LearningSession.status == "completed"),
                    func.coalesce(func.sum(LearningSession.duration_seconds), 0),
                ).where(LearningSession.user_id == user_id)
            )
        ).one()
        attempts = (
            await self.session.execute(
                select(
                    func.count(QuestionAttempt.id),
                    func.coalesce(func.sum(case((QuestionAttempt.is_correct, 1), else_=0)), 0),
                ).where(QuestionAttempt.user_id == user_id)
            )
        ).one()
        return {
            "total_sessions": int(sessions[0]),
            "total_study_minutes": round(float(sessions[1] or 0) / 60, 1),
            "questions_answered": int(attempts[0]),
            "correct_answers": int(attempts[1]),
        }

    # ---- misconceptions -----------------------------------------------------------------

    async def misconceptions(
        self,
        user_id: uuid.UUID,
        page: PageRequest,
        *,
        status: str | None = None,
        concept_id: uuid.UUID | None = None,
    ) -> Page[tuple[StudentMisconception, Misconception, Concept]]:
        filters = [StudentMisconception.user_id == user_id, Concept.user_id == user_id]
        if status:
            filters.append(StudentMisconception.status == status)
        if concept_id:
            filters.append(Misconception.concept_id == concept_id)
        base = (
            select(StudentMisconception, Misconception, Concept)
            .join(Misconception, Misconception.id == StudentMisconception.misconception_id)
            .join(Concept, Concept.id == Misconception.concept_id)
            .where(*filters)
        )
        total = int(
            await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        open_first = case((StudentMisconception.status.in_(("active", "recurring")), 0), else_=1)
        rows = (
            await self.session.execute(
                base.order_by(
                    open_first,
                    StudentMisconception.detected_at.desc(),
                    StudentMisconception.id,
                )
                .offset(page.offset)
                .limit(page.per_page)
            )
        ).all()
        return Page(
            items=[(r[0], r[1], r[2]) for r in rows],
            total=total,
            page=page.page,
            per_page=page.per_page,
        )

    async def misconception_counts(self, user_id: uuid.UUID) -> dict[str, int]:
        rows = (
            await self.session.execute(
                select(StudentMisconception.status, func.count(StudentMisconception.id))
                .where(StudentMisconception.user_id == user_id)
                .group_by(StudentMisconception.status)
            )
        ).all()
        return {str(status or "active"): int(n) for status, n in rows}

    # ---- AI usage -----------------------------------------------------------------------

    async def ai_usage(
        self, user_id: uuid.UUID, since: datetime
    ) -> Sequence[tuple[str, str, int, int, float, int]]:
        """(purpose, model, count, tokens, cost, failures) since ``since``."""
        return [
            (str(r[0]), str(r[1]), int(r[2]), int(r[3]), float(r[4] or 0.0), int(r[5]))
            for r in (
                await self.session.execute(
                    select(
                        AIInteraction.purpose,
                        AIInteraction.model,
                        func.count(AIInteraction.id),
                        func.coalesce(
                            func.sum(AIInteraction.input_tokens + AIInteraction.output_tokens), 0
                        ),
                        func.coalesce(func.sum(AIInteraction.cost_estimate), 0.0),
                        func.count().filter(AIInteraction.status != "success"),
                    )
                    .where(AIInteraction.user_id == user_id, AIInteraction.created_at >= since)
                    .group_by(AIInteraction.purpose, AIInteraction.model)
                )
            ).all()
        ]

    async def ai_daily_cost(
        self, user_id: uuid.UUID, since: datetime
    ) -> list[tuple[date, float, int]]:
        day = cast(func.timezone("UTC", AIInteraction.created_at), Date)
        rows = (
            await self.session.execute(
                select(day, func.coalesce(func.sum(AIInteraction.cost_estimate), 0.0), func.count())
                .where(AIInteraction.user_id == user_id, AIInteraction.created_at >= since)
                .group_by(day)
                .order_by(day)
            )
        ).all()
        return [(r[0], float(r[1] or 0.0), int(r[2])) for r in rows]
