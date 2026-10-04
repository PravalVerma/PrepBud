"""Content aggregate: `documents`, `document_sections`, `document_section_concepts`
(DATA_MODEL §3.9–3.11)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_col, jsonb_col, updated_at_col, uuid_pk

PROCESSING_STATUSES = ("pending", "processing", "ready", "failed")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_documents_user_id", "user_id"),
        Index("idx_documents_status", "processing_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    processing_status: Mapped[str | None] = mapped_column(Text, server_default=text("'pending'"))
    processing_metadata: Mapped[dict[str, Any] | None] = jsonb_col("{}")
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL")
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = created_at_col()
    updated_at: Mapped[datetime | None] = updated_at_col()


class DocumentSection(Base):
    __tablename__ = "document_sections"
    __table_args__ = (
        Index("idx_doc_sections_document_id", "document_id"),
        Index("idx_doc_sections_embedding_id", "embedding_id"),
        Index(
            "idx_doc_sections_content_fts",
            text("to_tsvector('english', content)"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    section_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_numbers: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    heading: Mapped[str | None] = mapped_column(Text)
    embedding_id: Mapped[str | None] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict[str, Any] | None] = jsonb_col("{}", name="metadata")
    created_at: Mapped[datetime | None] = created_at_col()


class DocumentSectionConcept(Base):
    __tablename__ = "document_section_concepts"
    __table_args__ = (
        UniqueConstraint("document_section_id", "concept_id"),
        Index("idx_dsc_section", "document_section_id"),
        Index("idx_dsc_concept", "concept_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_sections.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    relevance_score: Mapped[float | None] = mapped_column(Float, server_default=text("1.0"))
    created_at: Mapped[datetime | None] = created_at_col()
