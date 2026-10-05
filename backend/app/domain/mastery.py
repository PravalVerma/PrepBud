"""Dashboard schemas: mastery overview + heatmap, misconceptions, AI usage (§3.11–3.13)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class SubjectMasteryRead(BaseModel):
    id: uuid.UUID | None  # null = concepts not filed under a subject ("Unsorted")
    name: str
    avg_mastery: float
    concept_count: int
    mastered_count: int
    struggling_count: int


class OverallStats(BaseModel):
    total_concepts: int
    total_mastered: int
    in_progress: int
    avg_mastery: float
    streak_days: int
    total_study_minutes: float
    total_sessions: int
    questions_answered: int
    accuracy: float


class DayActivityRead(BaseModel):
    date: date
    sessions: int
    minutes: float
    questions: int
    concepts_practiced: int


class MasteryOverview(BaseModel):
    subjects: list[SubjectMasteryRead]
    overall_stats: OverallStats
    mastery_distribution: dict[str, int]
    recent_activity: list[DayActivityRead]
    misconceptions: dict[str, int]
    timezone: str
    generated_at: datetime


class HeatmapConcept(BaseModel):
    id: uuid.UUID
    name: str
    subject_id: uuid.UUID | None
    subject_name: str | None
    mastery_level: float
    label: str
    attempt_count: int
    last_assessed_at: datetime | None
    next_review_at: datetime | None
    cells: list[float | None]  # one per column; null = not yet assessed by then


class MasteryHeatmap(BaseModel):
    columns: list[date]  # week-end dates, oldest first; the last one is today
    concepts: list[HeatmapConcept]
    total_concepts: int
    truncated: bool


class MisconceptionInfo(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    concept_id: uuid.UUID
    concept_name: str


class MisconceptionEvidence(BaseModel):
    attempt_id: str | None = None
    description: str = ""
    detected_at: str | None = None


class StudentMisconceptionRead(BaseModel):
    id: uuid.UUID
    misconception: MisconceptionInfo
    status: str
    occurrence_count: int
    detected_at: datetime | None
    resolved_at: datetime | None
    evidence: list[MisconceptionEvidence]


class UsageBucket(BaseModel):
    cost: float
    count: int
    tokens: int
    failed: int


class AIUsage(BaseModel):
    period: str
    since: date
    total_cost_usd: float
    today_cost_usd: float
    daily_budget_usd: float
    total_tokens: int
    total_interactions: int
    failed_interactions: int
    by_purpose: dict[str, UsageBucket]
    by_model: dict[str, UsageBucket]
    daily: list[dict[str, Any]]
