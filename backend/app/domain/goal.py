"""Learning goal schemas (API_CONTRACT §3.7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

GoalType = Literal["mastery", "deadline", "exploration"]
GoalStatus = Literal["draft", "active", "completed", "paused", "abandoned"]
GOAL_STATUSES: tuple[str, ...] = ("draft", "active", "completed", "paused", "abandoned")

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
GoalDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
ConceptIds = Annotated[list[uuid.UUID], Field(max_length=200)]


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    description: GoalDescription | None = None
    goal_type: GoalType = "mastery"
    target_date: date | None = None
    subject_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    target_concept_ids: ConceptIds = Field(default_factory=list)

    @model_validator(mode="after")
    def _deadline_needs_date(self) -> GoalCreate:
        if self.goal_type == "deadline" and self.target_date is None:
            raise ValueError("a deadline goal needs a target_date")
        return self


class GoalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: GoalDescription | None = None
    goal_type: GoalType | None = None
    target_date: date | None = None
    status: GoalStatus | None = None
    subject_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    target_concept_ids: ConceptIds | None = None

    @model_validator(mode="after")
    def _not_null(self) -> GoalUpdate:
        for name in ("title", "goal_type", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class GoalProgressRead(BaseModel):
    concept_count: int
    mastered_count: int
    average_mastery: float
    progress: float
    days_remaining: int | None
    all_mastered: bool


class GoalConcept(BaseModel):
    id: uuid.UUID
    name: str
    mastery_level: float


class StudyPlanBrief(BaseModel):
    id: uuid.UUID
    total_items: int
    due_today: int
    generated_at: datetime | None


class GoalRead(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    goal_type: str
    target_date: date | None
    status: str
    subject_id: uuid.UUID | None
    course_id: uuid.UUID | None
    target_concept_ids: list[uuid.UUID]
    progress: GoalProgressRead
    created_at: datetime | None
    updated_at: datetime | None
    concepts: list[GoalConcept] | None = None  # detail view only
    study_plan: StudyPlanBrief | None = None  # after create/update
