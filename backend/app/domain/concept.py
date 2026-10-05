"""Concept schemas (API_CONTRACT §3.6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.common import mastery_label


class MasterySummary(BaseModel):
    level: float
    label: str
    last_assessed_at: datetime | None = None
    next_review_at: datetime | None = None

    @classmethod
    def build(
        cls, level: float | None, last: datetime | None, nxt: datetime | None
    ) -> MasterySummary:
        value = min(1.0, max(0.0, float(level or 0.0)))
        return cls(
            level=round(value, 4),
            label=mastery_label(value),
            last_assessed_at=last,
            next_review_at=nxt,
        )


class MasteryHistoryPoint(BaseModel):
    date: str
    mastery: float
    event: str | None = None


class MasteryDetail(MasterySummary):
    confidence: float = 0.0
    attempt_count: int = 0
    correct_count: int = 0
    streak: int = 0
    history: list[MasteryHistoryPoint] = Field(default_factory=list)


class ConceptListItem(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    difficulty_estimate: float
    subject_id: uuid.UUID | None
    chapter_id: uuid.UUID | None
    mastery: MasterySummary
    prerequisite_count: int
    document_count: int


class ConceptLink(BaseModel):
    id: uuid.UUID
    name: str
    mastery_level: float


class RelatedConcept(BaseModel):
    id: uuid.UUID
    name: str
    relationship: str


class MisconceptionLink(BaseModel):
    id: uuid.UUID
    name: str
    status: str


class ConceptDocumentLink(BaseModel):
    id: uuid.UUID
    title: str
    section_count: int


class ConceptDetail(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    difficulty_estimate: float
    subject_id: uuid.UUID | None
    chapter_id: uuid.UUID | None
    section_id: uuid.UUID | None
    mastery: MasteryDetail
    prerequisites: list[ConceptLink]
    # Concepts that list this one as a prerequisite (additive to the contract).
    dependents: list[ConceptLink]
    related_concepts: list[RelatedConcept]
    misconceptions: list[MisconceptionLink]
    documents: list[ConceptDocumentLink]
    created_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphNodeRead(BaseModel):
    id: uuid.UUID
    name: str
    mastery: float
    is_target: bool


class GraphEdgeRead(BaseModel):
    source: uuid.UUID
    target: uuid.UUID
    type: str


class ConceptGraph(BaseModel):
    nodes: list[GraphNodeRead]
    edges: list[GraphEdgeRead]


# --- Search ---------------------------------------------------------------------------


class SearchHitRead(BaseModel):
    section_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section_index: int
    heading: str | None
    page_numbers: list[int]
    snippet: str
    score: float
    matched_by: list[str]
