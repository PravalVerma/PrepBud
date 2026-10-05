"""Celery tasks run through the real worker runtime (ADR-010): document processing with
retries, and re-indexing. Tasks execute eagerly (`.apply()`), each in its own event loop
exactly as in a worker process, so these tests are synchronous."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from moto import mock_aws
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.llm_client import LLMClient
from app.ai.providers.base import LLMServerError
from app.config import Settings
from app.db.base import Base
from app.db.models import Document, User
from app.integrations.s3 import ObjectStorage
from app.workers import document_tasks, embedding_tasks, runtime
from app.workers.document_tasks import process_document, retry_countdown
from app.workers.embedding_tasks import reindex_document
from tests.fakes import FakeLLMProvider, FakeQueue, concept_text
from tests.integration.conftest import Infra
from tests.support import TEST_BUCKET, build_settings


async def _exec(url: str, *statements: Any) -> list[Any]:
    engine = create_async_engine(url, poolclass=NullPool)
    out = []
    try:
        async with engine.begin() as conn:
            for stmt in statements:
                out.append(await conn.execute(stmt))
    finally:
        await engine.dispose()
    return out


@pytest.fixture
def worker_env(infra: Infra, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    settings = build_settings(database_url=infra.database_url, redis_url=infra.redis_url)
    fake = FakeLLMProvider()
    monkeypatch.setattr(document_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(embedding_tasks, "get_settings", lambda: settings)
    # Let eager .apply() replay retries the way a worker would, instead of raising Retry.
    monkeypatch.setattr(process_document.app.conf, "task_eager_propagates", False)
    monkeypatch.setattr(
        runtime,
        "LLMClient",
        lambda s, recorder: LLMClient(s, recorder, providers={"fake": fake}),
    )
    with mock_aws():
        storage = ObjectStorage(settings)
        storage._client.create_bucket(Bucket=TEST_BUCKET)
        yield {"settings": settings, "fake": fake, "storage": storage}
    tables = ", ".join(Base.metadata.tables)
    asyncio.run(_exec(infra.database_url, text(f"TRUNCATE TABLE {tables} CASCADE")))


def seed_document(
    settings: Settings, storage: ObjectStorage, data: bytes, status: str = "processing"
) -> tuple[str, str]:
    user_id, document_id = uuid.uuid4(), uuid.uuid4()
    key = f"uploads/{user_id}/{document_id}/notes.txt"
    storage._client.put_object(Bucket=TEST_BUCKET, Key=key, Body=data, ContentType="text/plain")
    asyncio.run(
        _exec(
            settings.database_url,
            User.__table__.insert().values(
                id=user_id, auth_id=f"worker-{user_id}", email=f"{user_id}@example.com"
            ),
            Document.__table__.insert().values(
                id=document_id,
                user_id=user_id,
                title="notes",
                source_filename="notes.txt",
                mime_type="text/plain",
                file_size_bytes=len(data),
                s3_key=key,
                processing_status=status,
                processing_metadata={},
            ),
        )
    )
    return str(document_id), str(user_id)


def document_state(settings: Settings, document_id: str) -> tuple[str, dict[str, Any]]:
    [result] = asyncio.run(
        _exec(
            settings.database_url,
            select(Document.processing_status, Document.processing_metadata).where(
                Document.id == uuid.UUID(document_id)
            ),
        )
    )
    status, meta = result.one()
    return status, meta


TEXT = concept_text("Momentum", "Mass times velocity").encode()


def test_process_document_task_end_to_end(worker_env: dict[str, Any]) -> None:
    settings = worker_env["settings"]
    document_id, user_id = seed_document(settings, worker_env["storage"], TEXT)

    result = process_document.apply(args=[document_id, user_id], task_id="task-1")

    assert result.get() == "ready"
    status, meta = document_state(settings, document_id)
    assert status == "ready"
    assert meta["concept_count"] == 1
    # Qdrant isn't configured for the worker here → keyword-only, not a failure.
    assert meta["embedding_status"] == "skipped"


def test_process_document_task_retries_then_fails(worker_env: dict[str, Any]) -> None:
    settings = worker_env["settings"]
    fake: FakeLLMProvider = worker_env["fake"]
    fake.fail_with, fake.fail_times = LLMServerError("down"), -1
    document_id, user_id = seed_document(settings, worker_env["storage"], TEXT)

    result = process_document.apply(args=[document_id, user_id])

    assert result.get() == "failed"
    assert fake.calls.count("extraction") == process_document.max_retries + 1
    status, meta = document_state(settings, document_id)
    assert status == "failed" and meta["error"]["code"] == "LLM_UNAVAILABLE"


def test_process_document_task_recovers_on_retry(worker_env: dict[str, Any]) -> None:
    settings = worker_env["settings"]
    fake: FakeLLMProvider = worker_env["fake"]
    fake.fail_with, fake.fail_times = LLMServerError("blip"), 1
    document_id, user_id = seed_document(settings, worker_env["storage"], TEXT)
    assert process_document.apply(args=[document_id, user_id]).get() == "ready"


def test_task_skips_documents_not_in_processing(worker_env: dict[str, Any]) -> None:
    settings = worker_env["settings"]
    document_id, user_id = seed_document(settings, worker_env["storage"], TEXT, status="ready")
    assert process_document.apply(args=[document_id, user_id]).get() == "skipped"


def test_reindex_task(worker_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    settings = worker_env["settings"]
    document_id, user_id = seed_document(settings, worker_env["storage"], TEXT)
    assert process_document.apply(args=[document_id, user_id]).get() == "ready"
    # Without Qdrant configured, re-indexing has nothing to do.
    assert reindex_document.apply(args=[document_id, user_id]).get() == "skipped"


def test_runtime_requires_storage(infra: Infra) -> None:
    settings = build_settings(
        database_url=infra.database_url, redis_url=infra.redis_url, s3_bucket=""
    )

    async def enter() -> None:
        async with runtime.processing_runtime(settings):
            pass  # pragma: no cover

    with pytest.raises(runtime.StorageNotConfiguredError):
        asyncio.run(enter())


def test_retry_backoff() -> None:
    assert [retry_countdown(n) for n in range(5)] == [30, 60, 120, 240, 300]


def test_fake_queue_records() -> None:
    queue = FakeQueue()
    doc, user = uuid.uuid4(), uuid.uuid4()
    asyncio.run(queue.enqueue_reindex(doc, user))
    assert queue.reindexed == [(doc, user)]
