"""User aggregate: `users`, `student_profiles` (DATA_MODEL §3.1–3.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_col, jsonb_col, updated_at_col, uuid_pk

if TYPE_CHECKING:
    from app.db.models.curriculum import Subject


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("idx_users_auth_id", "auth_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    auth_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()

    profile: Mapped[StudentProfile | None] = relationship(
        back_populates="user", uselist=False, passive_deletes=True, lazy="raise"
    )
    subjects: Mapped[list[Subject]] = relationship(passive_deletes=True, lazy="raise")


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    grade_level: Mapped[str | None] = mapped_column(Text)
    difficulty_band: Mapped[str | None] = mapped_column(Text, server_default=text("'intermediate'"))
    language: Mapped[str | None] = mapped_column(Text, server_default=text("'en'"))
    timezone: Mapped[str | None] = mapped_column(Text, server_default=text("'UTC'"))
    preferences: Mapped[dict[str, Any] | None] = jsonb_col("{}")
    cumulative_stats: Mapped[dict[str, Any] | None] = jsonb_col("{}")
    onboarding_state: Mapped[str | None] = mapped_column(Text, server_default=text("'new'"))
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()

    user: Mapped[User] = relationship(back_populates="profile", lazy="raise")
