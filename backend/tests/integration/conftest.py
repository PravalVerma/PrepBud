"""Integration fixtures: real PostgreSQL + Redis, migrated schema, app + HTTP client.

Infrastructure comes from (in order of preference):
1. ``TEST_DATABASE_URL`` / ``TEST_REDIS_URL`` (e.g. CI service containers), or
2. testcontainers (``postgres:16-alpine`` / ``redis:7-alpine``) started once per run.

Each test gets a fresh app instance; all tables are truncated afterwards.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text

from app.core.security import JWKSCache
from app.db.base import Base
from app.main import create_app
from tests.support import build_settings, jwks

BACKEND_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Infra:
    database_url: str
    redis_url: str


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def infra() -> Iterator[Infra]:
    db_url = os.environ.get("TEST_DATABASE_URL")
    redis_url = os.environ.get("TEST_REDIS_URL")
    containers: list[Any] = []

    if not db_url:
        from testcontainers.community.postgres import PostgresContainer

        pg = PostgresContainer("postgres:16-alpine", driver="asyncpg")
        pg.start()
        containers.append(pg)
        db_url = pg.get_connection_url()
    if not redis_url:
        from testcontainers.community.redis import RedisContainer

        rd = RedisContainer("redis:7-alpine")
        rd.start()
        containers.append(rd)
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"

    command.upgrade(alembic_config(db_url), "head")
    try:
        yield Infra(database_url=db_url, redis_url=redis_url)
    finally:
        for c in reversed(containers):
            c.stop()


async def _fake_jwks_fetch(url: str) -> dict[str, Any]:
    return jwks()


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    """Override per test module/test to customise app settings."""
    return {}


@pytest.fixture
async def app(infra: Infra, settings_overrides: dict[str, Any]) -> AsyncIterator[FastAPI]:
    settings = build_settings(
        database_url=infra.database_url, redis_url=infra.redis_url, **settings_overrides
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.jwt_verifier.jwks_cache = JWKSCache(
            settings.jwks_url, settings.jwks_cache_ttl_seconds, fetcher=_fake_jwks_fetch
        )
        try:
            yield application
        finally:
            tables = ", ".join(Base.metadata.tables)
            async with application.state.engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
            await application.state.redis.flushdb()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def db(app: FastAPI) -> AsyncIterator[Any]:
    """A raw AsyncSession for asserting on / seeding database state directly."""
    async with app.state.sessionmaker() as session:
        yield session
