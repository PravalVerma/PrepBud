"""Mastery dashboard, misconceptions and AI usage (API_CONTRACT §3.11–3.13)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser, DbSession, PageParams
from app.core.exceptions import NotFoundError
from app.db.repositories.curriculum import SubjectRepository
from app.db.repositories.dashboard import DashboardRepository
from app.domain.common import Envelope, PaginatedEnvelope, envelope, paginated
from app.domain.mastery import (
    AIUsage,
    MasteryHeatmap,
    MasteryOverview,
    MisconceptionEvidence,
    MisconceptionInfo,
    StudentMisconceptionRead,
)
from app.services.dashboard import DashboardService

router = APIRouter(tags=["dashboard"])

MisconceptionStatus = Literal["active", "recurring", "resolved"]
EVIDENCE_SHOWN = 5


def _service(request: Request, session: DbSession) -> DashboardService:
    return DashboardService(session, request.app.state.settings, redis=request.app.state.redis)


@router.get("/mastery/overview", response_model=Envelope[MasteryOverview])
async def mastery_overview(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    days: int = Query(30, ge=7, le=365, description="Days of recent activity to return"),
) -> Envelope[MasteryOverview]:
    data = await _service(request, session).overview(user.id, days=days)
    return envelope(request, MasteryOverview.model_validate(data))


@router.get("/mastery/heatmap", response_model=Envelope[MasteryHeatmap])
async def mastery_heatmap(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    subject_id: uuid.UUID | None = None,
    weeks: int = Query(8, ge=2, le=26),
    limit: int = Query(100, ge=1, le=300),
) -> Envelope[MasteryHeatmap]:
    if subject_id and await SubjectRepository(session).get_by_id(subject_id, user.id) is None:
        raise NotFoundError("Subject not found")
    data = await _service(request, session).heatmap(
        user.id, subject_id=subject_id, weeks=weeks, limit=limit
    )
    return envelope(request, MasteryHeatmap.model_validate(data))


@router.get("/misconceptions", response_model=PaginatedEnvelope[StudentMisconceptionRead])
async def list_misconceptions(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    page: PageParams,
    misconception_status: MisconceptionStatus | None = Query(None, alias="status"),
    concept_id: uuid.UUID | None = None,
) -> PaginatedEnvelope[StudentMisconceptionRead]:
    result = await DashboardRepository(session).misconceptions(
        user.id, page, status=misconception_status, concept_id=concept_id
    )
    items = []
    for student, catalogue, concept in result.items:
        evidence = [
            MisconceptionEvidence(
                attempt_id=e.get("attempt_id"),
                description=str(e.get("description") or ""),
                detected_at=e.get("detected_at"),
            )
            for e in reversed(student.evidence or [])
            if isinstance(e, dict)
        ][:EVIDENCE_SHOWN]
        items.append(
            StudentMisconceptionRead(
                id=student.id,
                misconception=MisconceptionInfo(
                    id=catalogue.id,
                    name=catalogue.name,
                    description=catalogue.description,
                    concept_id=concept.id,
                    concept_name=concept.name,
                ),
                status=student.status or "active",
                occurrence_count=int(student.occurrence_count or 1),
                detected_at=student.detected_at,
                resolved_at=student.resolved_at,
                evidence=evidence,
            )
        )
    return paginated(request, items, total=result.total, page=result.page, per_page=result.per_page)


@router.get("/ai/usage", response_model=Envelope[AIUsage])
async def ai_usage(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    period: Literal["today", "week", "month"] = "today",
) -> Envelope[AIUsage]:
    data = await _service(request, session).ai_usage(user.id, period)
    return envelope(request, AIUsage.model_validate(data))
