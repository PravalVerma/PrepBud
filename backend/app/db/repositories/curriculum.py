"""Curriculum data access: subjects (user-owned) and courses / chapters / sections
(owned via the subject at the top of the hierarchy)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import aliased

from app.db.models import (
    Chapter,
    Concept,
    Course,
    Section,
    StudentConceptMastery,
    Subject,
)
from app.db.repositories.base import (
    BaseRepository,
    OwnedViaParentRepository,
    Page,
    PageRequest,
)


@dataclass(frozen=True, slots=True)
class SubjectWithStats:
    subject: Subject
    course_count: int
    concept_count: int
    avg_mastery: float


class SubjectRepository(BaseRepository[Subject]):
    model = Subject
    default_order_by = (Subject.created_at, Subject.id)

    def _with_stats(self, user_id: uuid.UUID) -> Select[Subject, int, int, float]:
        course_count = (
            select(func.count(Course.id))
            .where(Course.subject_id == Subject.id)
            .correlate(Subject)
            .scalar_subquery()
        )
        concept_count = (
            select(func.count(Concept.id))
            .where(Concept.subject_id == Subject.id, Concept.user_id == user_id)
            .correlate(Subject)
            .scalar_subquery()
        )
        # Average over every concept in the subject; concepts never assessed count as 0.
        scm = aliased(StudentConceptMastery)
        avg_mastery = (
            select(func.coalesce(func.avg(func.coalesce(scm.mastery_level, 0.0)), 0.0))
            .select_from(Concept)
            .outerjoin(scm, and_(scm.concept_id == Concept.id, scm.user_id == user_id))
            .where(Concept.subject_id == Subject.id, Concept.user_id == user_id)
            .correlate(Subject)
            .scalar_subquery()
        )
        return select(Subject, course_count, concept_count, avg_mastery).where(
            Subject.user_id == user_id
        )

    async def get_with_stats(
        self, subject_id: uuid.UUID, user_id: uuid.UUID
    ) -> SubjectWithStats | None:
        row = (
            await self.session.execute(self._with_stats(user_id).where(Subject.id == subject_id))
        ).one_or_none()
        if row is None:
            return None
        return SubjectWithStats(row[0], int(row[1]), int(row[2]), float(row[3]))

    async def paginate_with_stats(
        self, user_id: uuid.UUID, page: PageRequest
    ) -> Page[SubjectWithStats]:
        total = await self.session.scalar(
            select(func.count()).select_from(Subject).where(Subject.user_id == user_id)
        )
        rows = (
            await self.session.execute(
                self._with_stats(user_id)
                .order_by(*self.default_order_by)
                .offset(page.offset)
                .limit(page.per_page)
            )
        ).all()
        items = [SubjectWithStats(r[0], int(r[1]), int(r[2]), float(r[3])) for r in rows]
        return Page(items=items, total=total or 0, page=page.page, per_page=page.per_page)


class CourseRepository(OwnedViaParentRepository[Course]):
    model = Course
    default_order_by = (Course.sort_order, Course.created_at, Course.id)
    ownership_joins = ((Subject, Course.subject_id == Subject.id),)
    owner_column = Subject.user_id


class ChapterRepository(OwnedViaParentRepository[Chapter]):
    model = Chapter
    default_order_by = (Chapter.sort_order, Chapter.created_at, Chapter.id)
    ownership_joins = (
        (Course, Chapter.course_id == Course.id),
        (Subject, Course.subject_id == Subject.id),
    )
    owner_column = Subject.user_id


class SectionRepository(OwnedViaParentRepository[Section]):
    model = Section
    default_order_by = (Section.sort_order, Section.created_at, Section.id)
    ownership_joins = (
        (Chapter, Section.chapter_id == Chapter.id),
        (Course, Chapter.course_id == Course.id),
        (Subject, Course.subject_id == Subject.id),
    )
    owner_column = Subject.user_id
