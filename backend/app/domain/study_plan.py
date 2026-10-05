"""Study plan schemas (API_CONTRACT §3.10)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class ReviewItemRead(BaseModel):
    id: uuid.UUID
    concept_id: uuid.UUID
    concept_name: str
    scheduled_date: date
    priority: float
    status: str  # pending | overdue | completed | skipped
    kind: str  # learn | review
    goal_id: uuid.UUID | None
    mastery_level: float
    mastery_label: str
    next_review_at: datetime | None
    completed_at: datetime | None


class StudyPlanStats(BaseModel):
    total_items: int
    completed: int
    skipped: int
    overdue: int
    upcoming_today: int
    due_today: int
    reviews_due: int
    next_due_date: date | None
    estimated_minutes: int


class StudyPlanRead(BaseModel):
    id: uuid.UUID
    learning_goal_id: uuid.UUID | None
    status: str
    generated_at: datetime | None
    valid_until: datetime | None
    items: list[ReviewItemRead]
    stats: StudyPlanStats


class ReviewItemUpdate(BaseModel):
    """Skip, complete, un-skip (``pending``) and/or reschedule an item."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "completed", "skipped"] | None = None
    scheduled_date: date | None = None

    @model_validator(mode="after")
    def _something(self) -> ReviewItemUpdate:
        if self.status is None and self.scheduled_date is None:
            raise ValueError("provide status and/or scheduled_date")
        return self
