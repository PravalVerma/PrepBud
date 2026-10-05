"""Study plan (API_CONTRACT §3.10): today's work, upcoming items, overdue reviews first."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, DbSession, Plans
from app.domain.common import Envelope, envelope
from app.domain.study_plan import ReviewItemRead, ReviewItemUpdate, StudyPlanRead

router = APIRouter(tags=["study-plan"])


@router.get("/study-plan", response_model=Envelope[StudyPlanRead])
async def get_study_plan(
    request: Request, user: CurrentUser, session: DbSession, plans: Plans
) -> Envelope[StudyPlanRead]:
    """The active plan — generated on first use and refreshed once a day."""
    plan = await plans.ensure_plan(user.id)
    await session.commit()
    return envelope(request, StudyPlanRead.model_validate(await plans.view(user.id, plan)))


@router.post("/study-plan/regenerate", response_model=Envelope[StudyPlanRead])
async def regenerate_study_plan(
    request: Request, user: CurrentUser, session: DbSession, plans: Plans
) -> Envelope[StudyPlanRead]:
    plan = await plans.regenerate(user.id, reason="manual")
    await session.commit()
    return envelope(request, StudyPlanRead.model_validate(await plans.view(user.id, plan)))


@router.patch("/study-plan/items/{item_id}", response_model=Envelope[ReviewItemRead])
async def update_review_item(
    item_id: uuid.UUID,
    body: ReviewItemUpdate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    plans: Plans,
) -> Envelope[ReviewItemRead]:
    item, plan = await plans.update_item(
        user.id, item_id, status=body.status, scheduled_date=body.scheduled_date
    )
    await session.commit()
    view = await plans.view(user.id, plan)
    read = next(i for i in view["items"] if i["id"] == item.id)
    return envelope(request, ReviewItemRead.model_validate(read))
