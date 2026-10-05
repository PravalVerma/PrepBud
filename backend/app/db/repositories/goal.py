"""Learning goal data access (user-scoped)."""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement

from app.db.models import LearningGoal
from app.db.repositories.base import BaseRepository, Page, PageRequest


class GoalRepository(BaseRepository[LearningGoal]):
    model = LearningGoal
    default_order_by = (LearningGoal.created_at.desc(), LearningGoal.id)

    async def list_goals(
        self, user_id: uuid.UUID, page: PageRequest, *, status: str | None = None
    ) -> Page[LearningGoal]:
        filters: list[ColumnElement[bool]] = []
        if status:
            filters.append(LearningGoal.status == status)
        return await self.paginate(user_id, page, *filters)
