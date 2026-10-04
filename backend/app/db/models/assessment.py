"""Assessment + misconception aggregates: `questions`, `question_attempts`,
`misconceptions`, `student_misconceptions` (DATA_MODEL §3.15–3.18)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_col, jsonb_col, updated_at_col, uuid_pk

QUESTION_TYPES = ("mcq", "short_answer", "true_false", "worked_problem", "open_ended")
QUESTION_SOURCE_TYPES = ("generated", "templated", "imported")
MISCONCEPTION_SOURCE_TYPES = ("detected", "catalogued", "imported")
STUDENT_MISCONCEPTION_STATUSES = ("active", "resolved", "recurring")


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        Index("idx_questions_user_id", "user_id"),
        Index("idx_questions_concept_id", "concept_id"),
        Index("idx_questions_difficulty", "difficulty"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    question_type: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.5"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[Any] | None] = mapped_column(JSONB)
    correct_answer: Mapped[Any] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    hints: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    source_type: Mapped[str | None] = mapped_column(Text, server_default=text("'generated'"))
    metadata_: Mapped[dict[str, Any] | None] = jsonb_col("{}", name="metadata")
    created_at: Mapped[datetime | None] = created_at_col()


class QuestionAttempt(Base):
    __tablename__ = "question_attempts"
    __table_args__ = (
        Index("idx_attempts_user_id", "user_id"),
        Index("idx_attempts_question_id", "question_id"),
        Index("idx_attempts_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="SET NULL")
    )
    student_response: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"))
    ai_evaluation: Mapped[dict[str, Any] | None] = jsonb_col("{}")
    misconceptions_detected: Mapped[list[Any] | None] = jsonb_col("[]")
    time_taken_seconds: Mapped[int | None] = mapped_column(Integer)
    mastery_delta: Mapped[float | None] = mapped_column(Float, server_default=text("0.0"))
    attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Misconception(Base):
    __tablename__ = "misconceptions"
    __table_args__ = (Index("idx_misconceptions_concept_id", "concept_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    indicators: Mapped[list[Any] | None] = jsonb_col("[]")
    remediation_hints: Mapped[list[Any] | None] = jsonb_col("[]")
    source_type: Mapped[str | None] = mapped_column(Text, server_default=text("'detected'"))
    created_at: Mapped[datetime | None] = created_at_col()


class StudentMisconception(Base):
    __tablename__ = "student_misconceptions"
    __table_args__ = (
        Index("idx_student_misc_user", "user_id"),
        Index("idx_student_misc_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    misconception_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("misconceptions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'active'"))
    evidence: Mapped[list[Any] | None] = jsonb_col("[]")
    occurrence_count: Mapped[int | None] = mapped_column(Integer, server_default=text("1"))
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()
