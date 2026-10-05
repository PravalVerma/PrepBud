"""SQLAlchemy declarative base, async engine and session management (ADR-011).

Session-per-request: the `get_db_session` dependency (app.api.deps) opens one
`AsyncSession` per request from the sessionmaker created at application startup.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, FetchedValue, MetaData, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.config import Settings

# Match PostgreSQL's default constraint names so autogenerate comparisons against
# the hand-written initial migration are exact.
NAMING_CONVENTION = {
    "pk": "%(table_name)s_pkey",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }
    # Fetch server-generated values (ids, timestamps, trigger-set updated_at)
    # via RETURNING so objects are complete right after flush.
    __mapper_args__ = {"eager_defaults": True}


# --- Column helpers mirroring DATA_MODEL.md conventions ----------------------


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )


def created_at_col() -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), server_default=text("now()"))


def updated_at_col() -> Mapped[datetime | None]:
    # Maintained by the `set_updated_at` trigger (see initial migration).
    return mapped_column(
        DateTime(timezone=True), server_default=text("now()"), server_onupdate=FetchedValue()
    )


def jsonb_col(default_sql: str, *, name: str | None = None, nullable: bool = True) -> Any:
    """JSONB column with a literal default; ``name`` maps a differently named attribute
    (e.g. ``metadata_`` → ``metadata``, which SQLAlchemy reserves on declarative classes)."""
    default = text(f"'{default_sql}'::jsonb")
    if name:
        return mapped_column(name, JSONB, server_default=default, nullable=nullable)
    return mapped_column(JSONB, server_default=default, nullable=nullable)


# --- Engine / sessionmaker ----------------------------------------------------


def create_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": settings.database_echo, "pool_pre_ping": True}
    if settings.app_env == "test":
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = settings.database_pool_size
        kwargs["max_overflow"] = settings.database_max_overflow
    return create_async_engine(settings.database_url, **kwargs)


async def warm_pool(engine: AsyncEngine, size: int) -> int:
    """Open ``size`` pooled connections at startup and return them to the pool.

    asyncpg's SCRAM-SHA-256 handshake hashes in Python on the event loop (≈0.1–2 s per
    connection on a busy machine). Paying it at startup keeps the first burst of requests
    from stalling the whole API while the pool fills. Best effort: returns how many opened.
    """
    if size <= 0 or isinstance(engine.pool, NullPool):
        return 0
    opened: list[AsyncConnection] = []
    try:
        for _ in range(size):
            opened.append(await engine.connect())
    except Exception as exc:  # the health check reports a down database
        logging.getLogger(__name__).warning(
            "database pool warm-up failed", extra={"error": type(exc).__name__}
        )
    finally:
        for conn in opened:
            await conn.close()
    return len(opened)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
