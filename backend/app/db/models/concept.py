"""Concept graph aggregate: `concepts`, `concept_relationships` (DATA_MODEL §3.7–3.8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_col, jsonb_col, updated_at_col, uuid_pk

RELATIONSHIP_TYPES = ("prerequisite", "related", "generalisation", "specialisation")


class Concept(Base):
    __tablename__ = "concepts"
    __table_args__ = (
        Index("idx_concepts_user_id", "user_id"),
        Index("idx_concepts_section_id", "section_id"),
        Index("idx_concepts_chapter_id", "chapter_id"),
        Index("idx_concepts_subject_id", "subject_id"),
        # Parenthesised the way PostgreSQL reflects it so Alembic's comparison is
        # stable; equivalent to the DATA_MODEL §3.7 expression.
        Index(
            "idx_concepts_name_fts",
            text("to_tsvector('english', (name || ' ') || COALESCE(description, ''))"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    difficulty_estimate: Mapped[float | None] = mapped_column(Float, server_default=text("0.5"))
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sections.id", ondelete="SET NULL")
    )
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="SET NULL")
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL")
    )
    metadata_: Mapped[dict[str, Any] | None] = jsonb_col("{}", name="metadata")
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()


class ConceptRelationship(Base):
    __tablename__ = "concept_relationships"
    __table_args__ = (
        # PostgreSQL truncates the auto-generated name to 63 characters.
        UniqueConstraint(
            "source_concept_id",
            "target_concept_id",
            "relationship_type",
            name="concept_relationships_source_concept_id_target_concept_id_r_key",
        ),
        CheckConstraint(
            "source_concept_id <> target_concept_id", name="concept_relationships_check"
        ),
        Index("idx_concept_rel_source", "source_concept_id"),
        Index("idx_concept_rel_target", "target_concept_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    target_concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    strength: Mapped[float | None] = mapped_column(Float, server_default=text("1.0"))
    created_at: Mapped[datetime | None] = created_at_col()
