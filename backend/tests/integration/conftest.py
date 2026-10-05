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
from moto import mock_aws
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import get_prompt_manager
from app.core.security import JWKSCache
from app.db.base import Base
from app.integrations.qdrant import SectionVectorStore
from app.integrations.s3 import ObjectStorage
from app.main import create_app
from app.services.content.document_processor import DocumentProcessor
from tests.fakes import FakeLLMProvider, FakeOCR, FakeQueue
from tests.support import TEST_BUCKET, build_settings, jwks

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
        try:
            from testcontainers.community.postgres import PostgresContainer
        except ImportError:  # testcontainers < 4.14
            from testcontainers.postgres import PostgresContainer

        pg = PostgresContainer("postgres:16-alpine", driver="asyncpg")
        pg.start()
        containers.append(pg)
        db_url = pg.get_connection_url()
    if not redis_url:
        try:
            from testcontainers.community.redis import RedisContainer
        except ImportError:  # testcontainers < 4.14
            from testcontainers.redis import RedisContainer

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
def fake_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def fake_ocr() -> FakeOCR:
    return FakeOCR()


@pytest.fixture
async def app(
    infra: Infra,
    settings_overrides: dict[str, Any],
    fake_llm: FakeLLMProvider,
    fake_queue: FakeQueue,
) -> AsyncIterator[FastAPI]:
    settings = build_settings(
        database_url=infra.database_url, redis_url=infra.redis_url, **settings_overrides
    )
    application = create_app(settings)
    # S3 is moto (in-process), Qdrant runs in-memory, the LLM and task queue are fakes.
    with mock_aws():
        async with application.router.lifespan_context(application):
            application.state.jwt_verifier.jwks_cache = JWKSCache(
                settings.jwks_url, settings.jwks_cache_ttl_seconds, fetcher=_fake_jwks_fetch
            )
            storage = ObjectStorage(settings)  # real client config, moto backend
            storage._client.create_bucket(Bucket=TEST_BUCKET)
            application.state.storage = storage
            application.state.vector_store = SectionVectorStore(
                AsyncQdrantClient(location=":memory:"),
                settings.qdrant_sections_collection,
                settings.embedding_dimensions,
            )
            application.state.llm = LLMClient(
                settings, application.state.ai_recorder, providers={"fake": fake_llm}
            )
            application.state.task_queue = fake_queue
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


@pytest.fixture
def processor(app: FastAPI, fake_ocr: FakeOCR, fake_queue: FakeQueue) -> DocumentProcessor:
    """The document pipeline wired to the test app's database, storage and fakes."""
    state = app.state
    return DocumentProcessor(
        settings=state.settings,
        sessionmaker=state.sessionmaker,
        storage=state.storage,
        llm=state.llm,
        recorder=state.ai_recorder,
        prompts=get_prompt_manager(),
        ocr=fake_ocr,
        vectors=state.vector_store,
        redis=state.redis,
        queue=fake_queue,
    )


class Uploader:
    """Drives the upload flow like the browser would: upload-url → PUT → confirm."""

    def __init__(self, app: FastAPI, client: httpx.AsyncClient, queue: FakeQueue) -> None:
        self.app, self.client, self.queue = app, client, queue

    async def request_url(
        self,
        headers: dict[str, str],
        *,
        filename: str = "notes.txt",
        mime_type: str = "text/plain",
        size: int = 100,
        **extra: Any,
    ) -> httpx.Response:
        return await self.client.post(
            "/api/v1/documents/upload-url",
            json={"filename": filename, "mime_type": mime_type, "file_size_bytes": size, **extra},
            headers=headers,
        )

    def put(self, key: str, data: bytes, mime_type: str) -> None:
        storage: ObjectStorage = self.app.state.storage
        storage._client.put_object(Bucket=TEST_BUCKET, Key=key, Body=data, ContentType=mime_type)

    async def upload(
        self,
        headers: dict[str, str],
        data: bytes,
        *,
        filename: str = "notes.txt",
        mime_type: str = "text/plain",
        confirm: bool = True,
        **extra: Any,
    ) -> dict[str, Any]:
        resp = await self.request_url(
            headers, filename=filename, mime_type=mime_type, size=len(data), **extra
        )
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()["data"]
        self.put(payload["s3_key"], data, mime_type)
        if confirm:
            confirm_resp = await self.client.post(
                f"/api/v1/documents/{payload['document_id']}/confirm-upload", headers=headers
            )
            assert confirm_resp.status_code == 202, confirm_resp.text
            payload["task_id"] = confirm_resp.json()["data"]["task_id"]
        return payload


@pytest.fixture
def uploader(app: FastAPI, client: httpx.AsyncClient, fake_queue: FakeQueue) -> Uploader:
    return Uploader(app, client, fake_queue)


async def drain(processor: DocumentProcessor, queue: FakeQueue) -> list[str]:
    """Run every queued document task (what a Celery worker would do)."""
    outcomes = []
    while queue.documents:
        document_id, user_id, task_id = queue.documents.pop(0)
        outcomes.append(str(await processor.process(document_id, user_id, task_id=task_id)))
    return outcomes
