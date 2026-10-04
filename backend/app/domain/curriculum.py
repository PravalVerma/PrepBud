"""Curriculum schemas: Subject, Course, Chapter, Section (API_CONTRACT §3.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.common import ORMModel
from app.domain.types import Description, Icon, Name

SortOrder = Annotated[int, Field(ge=0, le=1_000_000)]


class _UpdateBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _name_not_null(self) -> _UpdateBase:
        if "name" in self.model_fields_set and getattr(self, "name", None) is None:
            raise ValueError("name cannot be null")
        return self


# --- Subject -----------------------------------------------------------------------


class SubjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Description | None = None
    icon: Icon | None = None


class SubjectUpdate(_UpdateBase):
    name: Name | None = None
    description: Description | None = None
    icon: Icon | None = None


class SubjectRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    icon: str | None
    course_count: int = 0
    concept_count: int = 0
    avg_mastery: float = 0.0
    created_at: datetime | None
    updated_at: datetime | None


# --- Course / Chapter / Section ------------------------------------------------------


class _NodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Description | None = None
    sort_order: SortOrder = 0


class _NodeUpdate(_UpdateBase):
    name: Name | None = None
    description: Description | None = None
    sort_order: SortOrder | None = None


class _NodeRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    sort_order: int | None
    created_at: datetime | None
    updated_at: datetime | None


class CourseCreate(_NodeCreate):
    pass


class CourseUpdate(_NodeUpdate):
    pass


class CourseRead(_NodeRead):
    subject_id: uuid.UUID


class ChapterCreate(_NodeCreate):
    pass


class ChapterUpdate(_NodeUpdate):
    pass


class ChapterRead(_NodeRead):
    course_id: uuid.UUID


class SectionCreate(_NodeCreate):
    pass


class SectionUpdate(_NodeUpdate):
    pass


class SectionRead(_NodeRead):
    chapter_id: uuid.UUID
