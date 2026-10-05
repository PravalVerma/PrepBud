"""Celery wiring: configuration and enqueueing by task name."""

from __future__ import annotations

import uuid
from typing import Any

from app.workers.celery_app import TASK_MODULES, celery_app, create_celery
from app.workers.queue import PROCESS_DOCUMENT, REINDEX_DOCUMENT, CeleryTaskQueue
from tests.support import build_settings


class FakeCelery:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send_task(self, name: str, **kwargs: Any) -> None:
        self.sent.append((name, kwargs))


def test_reliability_settings() -> None:
    settings = build_settings(redis_url="redis://broker:6379/3")
    app = create_celery(settings)
    conf = app.conf
    assert conf.broker_url == "redis://broker:6379/3"
    assert conf.result_backend == "redis://broker:6379/3"
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_serializer == "json" and conf.accept_content == ["json"]
    assert conf.task_soft_time_limit == settings.content.task_soft_time_limit_seconds
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit
    assert conf.worker_hijack_root_logger is False
    assert conf.task_default_queue == "default"
    assert create_celery(build_settings(celery_queue="e2e")).conf.task_default_queue == "e2e"


def test_tasks_registered_on_module_app() -> None:
    import app.workers.document_tasks
    import app.workers.embedding_tasks  # noqa: F401

    assert {PROCESS_DOCUMENT, REINDEX_DOCUMENT} <= set(celery_app.tasks)
    assert TASK_MODULES == ["app.workers.document_tasks", "app.workers.embedding_tasks"]


async def test_queue_sends_by_name_with_ids() -> None:
    fake = FakeCelery()
    queue = CeleryTaskQueue(fake)  # type: ignore[arg-type]
    doc, user = uuid.uuid4(), uuid.uuid4()

    task_id = await queue.enqueue_document(doc, user, task_id="fixed-id")
    generated = await queue.enqueue_reindex(doc, user, countdown=90)

    assert task_id == "fixed-id"
    assert fake.sent[0] == (
        PROCESS_DOCUMENT,
        {"args": [str(doc), str(user)], "task_id": "fixed-id", "countdown": None},
    )
    name, kwargs = fake.sent[1]
    assert name == REINDEX_DOCUMENT and kwargs["countdown"] == 90
    assert kwargs["task_id"] == generated and uuid.UUID(generated)
