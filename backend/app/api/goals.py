"""Learning goals (API_CONTRACT §3.7). Every change regenerates the study plan (AC-6.3)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, PageParams, Plans
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import Concept, LearningGoal
from app.db.repositories.curriculum import CourseRepository, SubjectRepository
from app.db.repositories.goal import GoalRepository
from app.domain.common import Envelope, PaginatedEnvelope, envelope, paginated
from app.domain.goal import (
    GoalConcept,
    GoalCreate,
    GoalProgressRead,
    GoalRead,
    GoalStatus,
    GoalUpdate,
    StudyPlanBrief,
)
from app.services.study_plan.plan_service import StudyPlanService, goal_concept_ids

router = APIRouter(tags=["goals"])


async def _check_scope(
    session: DbSession,
    user_id: uuid.UUID,
    *,
    subject_id: uuid.UUID | None,
    course_id: uuid.UUID | None,
    concept_ids: list[uuid.UUID] | None,
) -> None:
    """Referenced subject/course/concepts must be the user's own — otherwise 404."""
    if subject_id and await SubjectRepository(session).get_by_id(subject_id, user_id) is None:
        raise NotFoundError("Subject not found")
    if course_id and await CourseRepository(session).get_by_id(course_id, user_id) is None:
        raise NotFoundError("Course not found")
    if concept_ids:
        owned = set(
            (
                await session.scalars(
                    select(Concept.id).where(
                        Concept.user_id == user_id, Concept.id.in_(concept_ids)
                    )
                )
            ).all()
        )
        missing = [str(c) for c in concept_ids if c not in owned]
        if missing:
            raise NotFoundError("Concept not found", details={"concept_ids": missing})


async def _get(session: DbSession, user_id: uuid.UUID, goal_id: uuid.UUID) -> LearningGoal:
    goal = await GoalRepository(session).get_by_id(goal_id, user_id)
    if goal is None:
        raise NotFoundError("Goal not found")
    return goal


async def _read(
    plans: StudyPlanService,
    user_id: uuid.UUID,
    goal: LearningGoal,
    *,
    detail: bool = False,
    plan_stats: dict[str, Any] | None = None,
) -> GoalRead:
    progress = (await plans.goal_progress(user_id, [goal]))[goal.id]
    concepts = None
    if detail:
        ids = await goal_concept_ids(plans.session, user_id, goal)
        rows = await plans.session.execute(
            select(Concept.id, Concept.name).where(Concept.id.in_(ids))
        )
        names = {row[0]: row[1] for row in rows}
        levels = await plans.tracker.levels(user_id, ids) if ids else {}
        concepts = [
            GoalConcept(id=c, name=names.get(c, ""), mastery_level=round(levels.get(c, 0.0), 4))
            for c in ids
        ]
    return GoalRead(
        id=goal.id,
        title=goal.title,
        description=goal.description,
        goal_type=goal.goal_type or "mastery",
        target_date=goal.target_date,
        status=goal.status or "active",
        subject_id=goal.subject_id,
        course_id=goal.course_id,
        target_concept_ids=list(goal.target_concepts or []),
        progress=GoalProgressRead(
            concept_count=progress.concept_count,
            mastered_count=progress.mastered_count,
            average_mastery=progress.average_mastery,
            progress=progress.progress,
            days_remaining=progress.days_remaining,
            all_mastered=progress.all_mastered,
        ),
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        concepts=concepts,
        study_plan=StudyPlanBrief(**plan_stats) if plan_stats else None,
    )


async def _replan(plans: StudyPlanService, user_id: uuid.UUID, reason: str) -> dict[str, Any]:
    plan = await plans.regenerate(user_id, reason=reason)
    stats = (await plans.view(user_id, plan))["stats"]
    return {
        "id": plan.id,
        "total_items": stats["total_items"],
        "due_today": stats["due_today"],
        "generated_at": plan.generated_at,
    }


@router.get("/goals", response_model=PaginatedEnvelope[GoalRead])
async def list_goals(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    plans: Plans,
    page: PageParams,
    goal_status: GoalStatus | None = Query(None, alias="status"),
) -> PaginatedEnvelope[GoalRead]:
    result = await GoalRepository(session).list_goals(user.id, page, status=goal_status)
    items = [await _read(plans, user.id, g) for g in result.items]
    return paginated(request, items, total=result.total, page=result.page, per_page=result.per_page)


@router.post("/goals", response_model=Envelope[GoalRead], status_code=status.HTTP_201_CREATED)
async def create_goal(
    body: GoalCreate, request: Request, user: CurrentUser, session: DbSession, plans: Plans
) -> Envelope[GoalRead]:
    await _check_scope(
        session,
        user.id,
        subject_id=body.subject_id,
        course_id=body.course_id,
        concept_ids=body.target_concept_ids,
    )
    goal = await GoalRepository(session).create(
        user.id,
        title=body.title,
        description=body.description,
        goal_type=body.goal_type,
        target_date=body.target_date,
        status="active",
        subject_id=body.subject_id,
        course_id=body.course_id,
        target_concepts=list(dict.fromkeys(body.target_concept_ids)) or None,
    )
    plan = await _replan(plans, user.id, "goal_created")
    await session.commit()
    await session.refresh(goal)
    return envelope(request, await _read(plans, user.id, goal, detail=True, plan_stats=plan))


@router.get("/goals/{goal_id}", response_model=Envelope[GoalRead])
async def get_goal(
    goal_id: uuid.UUID, request: Request, user: CurrentUser, session: DbSession, plans: Plans
) -> Envelope[GoalRead]:
    goal = await _get(session, user.id, goal_id)
    return envelope(request, await _read(plans, user.id, goal, detail=True))


@router.patch("/goals/{goal_id}", response_model=Envelope[GoalRead])
async def update_goal(
    goal_id: uuid.UUID,
    body: GoalUpdate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    plans: Plans,
) -> Envelope[GoalRead]:
    goal = await _get(session, user.id, goal_id)
    changes = body.model_dump(exclude_unset=True)
    await _check_scope(
        session,
        user.id,
        subject_id=changes.get("subject_id"),
        course_id=changes.get("course_id"),
        concept_ids=changes.get("target_concept_ids"),
    )
    if "target_concept_ids" in changes:
        ids = changes.pop("target_concept_ids") or []
        changes["target_concepts"] = list(dict.fromkeys(ids)) or None
    if (changes.get("goal_type") or goal.goal_type) == "deadline" and not (
        changes.get("target_date", goal.target_date)
    ):
        raise ValidationError(
            "A deadline goal needs a target date", details={"field": "target_date"}
        )
    await GoalRepository(session).update(goal, changes)
    plan = await _replan(plans, user.id, "goal_updated")
    await session.commit()
    await session.refresh(goal)
    return envelope(request, await _read(plans, user.id, goal, detail=True, plan_stats=plan))


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: uuid.UUID, user: CurrentUser, session: DbSession, plans: Plans
) -> Response:
    goal = await _get(session, user.id, goal_id)
    await GoalRepository(session).delete(goal)
    await plans.regenerate(user.id, reason="goal_deleted")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
