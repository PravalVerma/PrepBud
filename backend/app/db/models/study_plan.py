"""Study plan aggregate: `study_plans`, `review_items` (DATA_MODEL §3.20–3.21)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_col, jsonb_col, updated_at_col, uuid_pk

STUDY_PLAN_STATUSES = ("active", "superseded", "completed")
REVIEW_ITEM_STATUSES = ("pending", "completed", "skipped", "overdue")


class StudyPlan(Base):
    __tablename__ = "study_plans"
    __table_args__ = (
        Index("idx_study_plans_user_id", "user_id"),
        Index("idx_study_plans_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    learning_goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="SET NULL")
    )
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'active'"))
    generation_metadata: Mapped[dict[str, Any] | None] = jsonb_col("{}")
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        Index("idx_review_items_plan", "study_plan_id"),
        Index("idx_review_items_concept", "concept_id"),
        Index("idx_review_items_date", "scheduled_date"),
        Index("idx_review_items_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    study_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    priority: Mapped[float | None] = mapped_column(Float, server_default=text("0.5"))
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'pending'"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()
