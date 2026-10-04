"""Mastery aggregate: `student_concept_mastery` (DATA_MODEL §3.19).

Invariant: exactly one record per (user, concept); mastery_level ∈ [0.0, 1.0].
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_col, jsonb_col, updated_at_col, uuid_pk


class StudentConceptMastery(Base):
    __tablename__ = "student_concept_mastery"
    __table_args__ = (
        UniqueConstraint("user_id", "concept_id"),
        Index("idx_mastery_user_id", "user_id"),
        Index("idx_mastery_concept_id", "concept_id"),
        Index("idx_mastery_next_review", "next_review_at"),
        Index("idx_mastery_level", "mastery_level"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    mastery_level: Mapped[float | None] = mapped_column(Float, server_default=text("0.0"))
    confidence: Mapped[float | None] = mapped_column(Float, server_default=text("0.0"))
    attempt_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    correct_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    streak: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    # Spaced repetition (SM-2 compatible)
    ease_factor: Mapped[float | None] = mapped_column(Float, server_default=text("2.5"))
    interval_days: Mapped[float | None] = mapped_column(Float, server_default=text("1.0"))
    repetition_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    last_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history: Mapped[list[Any] | None] = jsonb_col("[]")
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()
