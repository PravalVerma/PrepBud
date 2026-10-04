"""AC-2.5 — the Alembic migration produces exactly the DATA_MODEL.md schema.

* Autogenerate comparison: ORM models ⇄ migrated database have zero differences
  (tables, columns, types, nullability, PK/FK/unique constraints, indexes).
* Explicit checks for what autogenerate cannot see: the uuid-ossp extension,
  column server defaults, the CHECK constraint, GIN full-text indexes and the
  `updated_at` triggers.
* Round trip: downgrade to base and upgrade again on a scratch database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import models  # noqa: F401
from app.db.base import Base
from tests.integration.conftest import Infra, alembic_config

DATA_MODEL_TABLES = {
    "users",
    "student_profiles",
    "subjects",
    "courses",
    "chapters",
    "sections",
    "concepts",
    "concept_relationships",
    "documents",
    "document_sections",
    "document_section_concepts",
    "learning_goals",
    "learning_sessions",
    "session_events",
    "questions",
    "question_attempts",
    "misconceptions",
    "student_misconceptions",
    "student_concept_mastery",
    "study_plans",
    "review_items",
    "ai_traces",
    "ai_interactions",
}
TABLES_WITH_UPDATED_AT = {
    "users",
    "student_profiles",
    "subjects",
    "courses",
    "chapters",
    "sections",
    "concepts",
    "documents",
    "learning_goals",
    "learning_sessions",
    "student_misconceptions",
    "student_concept_mastery",
    "study_plans",
    "review_items",
}


@pytest.fixture
async def conn(infra: Infra) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(infra.database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        yield connection
    await engine.dispose()


def _diff(sync_conn: Connection) -> list[object]:
    ctx = MigrationContext.configure(
        sync_conn,
        opts={
            "compare_type": True,
            "include_object": lambda o, n, t, r, c: not (t == "table" and n == "alembic_version"),
        },
    )
    return list(compare_metadata(ctx, Base.metadata))


async def test_models_match_migrated_schema(conn: AsyncConnection) -> None:
    assert await conn.run_sync(_diff) == []


async def test_all_23_tables_exist(conn: AsyncConnection) -> None:
    rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    tables = {r[0] for r in rows} - {"alembic_version"}
    assert tables == DATA_MODEL_TABLES
    assert len(tables) == 23
    assert set(Base.metadata.tables) == DATA_MODEL_TABLES


async def test_uuid_extension_and_pk_defaults(conn: AsyncConnection) -> None:
    ext = await conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'uuid-ossp'"))
    assert ext == 1
    rows = await conn.execute(
        text(
            "SELECT table_name, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'id'"
        )
    )
    defaults = {r[0]: r[1] for r in rows}
    assert set(defaults) >= DATA_MODEL_TABLES
    assert all(defaults[t] == "uuid_generate_v4()" for t in DATA_MODEL_TABLES)


@pytest.mark.parametrize(
    ("table", "column", "expected"),
    [
        ("users", "is_active", "true"),
        ("student_profiles", "difficulty_band", "'intermediate'::text"),
        ("student_profiles", "onboarding_state", "'new'::text"),
        ("student_profiles", "preferences", "'{}'::jsonb"),
        ("concepts", "difficulty_estimate", "0.5"),
        ("documents", "processing_status", "'pending'::text"),
        ("learning_sessions", "status", "'initialising'::text"),
        ("question_attempts", "misconceptions_detected", "'[]'::jsonb"),
        ("student_concept_mastery", "ease_factor", "2.5"),
        ("student_concept_mastery", "interval_days", "1.0"),
        ("ai_interactions", "status", "'success'::text"),
        ("subjects", "created_at", "now()"),
    ],
)
async def test_server_defaults(
    conn: AsyncConnection, table: str, column: str, expected: str
) -> None:
    default = await conn.scalar(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    assert default == expected


async def test_check_constraint_and_fts_indexes(conn: AsyncConnection) -> None:
    check = await conn.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'concept_relationships_check'"
        )
    )
    assert check == "CHECK ((source_concept_id <> target_concept_id))"

    rows = await conn.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE indexname IN ('idx_concepts_name_fts', 'idx_doc_sections_content_fts')"
        )
    )
    defs = {r[0]: r[1] for r in rows}
    assert "USING gin" in defs["idx_concepts_name_fts"]
    assert "COALESCE(description" in defs["idx_concepts_name_fts"]
    assert (
        "USING gin (to_tsvector('english'::regconfig, content))"
        in defs["idx_doc_sections_content_fts"]
    )


async def test_updated_at_triggers_on_every_mutable_table(conn: AsyncConnection) -> None:
    rows = await conn.execute(
        text(
            "SELECT event_object_table FROM information_schema.triggers "
            "WHERE trigger_name LIKE 'trg_%_updated_at' AND event_manipulation = 'UPDATE'"
        )
    )
    assert {r[0] for r in rows} == TABLES_WITH_UPDATED_AT


async def test_downgrade_and_upgrade_round_trip(infra: Infra) -> None:
    url = make_url(infra.database_url)
    scratch = f"migration_rt_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        await c.execute(text(f'CREATE DATABASE "{scratch}"'))
    scratch_url = url.set(database=scratch).render_as_string(hide_password=False)
    try:
        cfg = alembic_config(scratch_url)
        # Alembic's env.py drives its own event loop; run it off this one.
        import asyncio

        await asyncio.to_thread(command.upgrade, cfg, "head")
        await asyncio.to_thread(command.downgrade, cfg, "base")

        eng = create_async_engine(scratch_url, poolclass=NullPool)
        async with eng.connect() as c:
            remaining = await c.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
        assert remaining == 0

        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with eng.connect() as c:
            assert await c.run_sync(_diff) == []
        await eng.dispose()
    finally:
        async with admin.connect() as c:
            await c.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()
