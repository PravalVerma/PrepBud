"""Learning session data access (user-scoped)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, select

from app.db.models import LearningSession, SessionEvent
from app.db.repositories.base import BaseRepository, Page, PageRequest

MAX_EVENTS = 500


class SessionRepository(BaseRepository[LearningSession]):
    model = LearningSession
    default_order_by = (LearningSession.started_at.desc(), LearningSession.id)

    async def list_sessions(
        self, user_id: uuid.UUID, page: PageRequest, *, status: str | None = None
    ) -> Page[LearningSession]:
        filters: list[ColumnElement[bool]] = []
        if status:
            filters.append(LearningSession.status == status)
        return await self.paginate(user_id, page, *filters)

    async def events(
        self, session_id: uuid.UUID, limit: int = MAX_EVENTS
    ) -> Sequence[SessionEvent]:
        """Ordered event log (the caller has already checked ownership of the session)."""
        return (
            await self.session.scalars(
                select(SessionEvent)
                .where(SessionEvent.session_id == session_id)
                .order_by(SessionEvent.event_index)
                .limit(limit)
            )
        ).all()
