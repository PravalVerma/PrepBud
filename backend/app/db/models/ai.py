"""AI observability aggregate: `ai_traces`, `ai_interactions` (DATA_MODEL §3.22–3.23).

AI interactions are immutable once logged (DOMAIN_MODEL rule 7).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_col, jsonb_col, uuid_pk

TRACE_STATUSES = ("started", "completed", "failed")
INTERACTION_STATUSES = ("success", "error", "timeout")


class AITrace(Base):
    __tablename__ = "ai_traces"
    __table_args__ = (
        Index("idx_ai_traces_user_id", "user_id"),
        Index("idx_ai_traces_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="SET NULL")
    )
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'started'"))
    total_tokens: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    total_cost: Mapped[float | None] = mapped_column(Float, server_default=text("0.0"))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any] | None] = jsonb_col("{}", name="metadata")


class AIInteraction(Base):
    __tablename__ = "ai_interactions"
    __table_args__ = (
        Index("idx_ai_interactions_trace_id", "trace_id"),
        Index("idx_ai_interactions_user_id", "user_id"),
        Index("idx_ai_interactions_purpose", "purpose"),
        Index("idx_ai_interactions_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    trace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    response_hash: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_estimate: Mapped[float | None] = mapped_column(Float, server_default=text("0.0"))
    latency_ms: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'success'"))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = jsonb_col("{}", name="metadata")
    created_at: Mapped[datetime | None] = created_at_col()
