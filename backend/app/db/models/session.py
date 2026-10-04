"""Session aggregate: `learning_goals`, `learning_sessions`, `session_events`
(DATA_MODEL §3.12–3.14)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_col, jsonb_col, updated_at_col, uuid_pk

GOAL_TYPES = ("mastery", "deadline", "exploration")
GOAL_STATUSES = ("draft", "active", "completed", "paused", "abandoned")
SESSION_TYPES = ("teach", "practice", "review", "mixed")
SESSION_STATUSES = ("initialising", "active", "paused", "completed", "abandoned")
SESSION_EVENT_TYPES = (
    "explanation_given",
    "question_asked",
    "answer_received",
    "hint_given",
    "misconception_detected",
    "mastery_updated",
    "concept_changed",
    "session_paused",
    "session_resumed",
    "follow_up_question",
    "follow_up_answer",
)


class LearningGoal(Base):
    __tablename__ = "learning_goals"
    __table_args__ = (
        Index("idx_learning_goals_user_id", "user_id"),
        Index("idx_learning_goals_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    goal_type: Mapped[str | None] = mapped_column(Text, server_default=text("'mastery'"))
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'active'"))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL")
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL")
    )
    target_concepts: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    metadata_: Mapped[dict[str, Any] | None] = jsonb_col("{}", name="metadata")
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()


class LearningSession(Base):
    __tablename__ = "learning_sessions"
    __table_args__ = (
        Index("idx_sessions_user_id", "user_id"),
        Index("idx_sessions_status", "status"),
        Index("idx_sessions_started", "started_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    learning_goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="SET NULL")
    )
    session_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'initialising'"))
    objective: Mapped[dict[str, Any] | None] = jsonb_col("{}")
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    concepts_covered: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    interaction_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict[str, Any] | None] = jsonb_col("{}", name="metadata")
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()


class SessionEvent(Base):
    """Append-only (DOMAIN_MODEL rule 4): never updated or deleted once written."""

    __tablename__ = "session_events"
    __table_args__ = (
        Index("idx_session_events_session_id", "session_id"),
        Index("idx_session_events_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="CASCADE"), nullable=False
    )
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    concept_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("concepts.id", ondelete="SET NULL")
    )
    payload: Mapped[dict[str, Any]] = jsonb_col("{}", nullable=False)
    created_at: Mapped[datetime | None] = created_at_col()
