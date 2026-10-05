"""Celery application (ADR-010). Redis is the broker and result backend.

Run a worker:  ``celery -A app.workers.celery_app worker -l info``
(on Windows add ``--pool=solo``). Scheduled jobs need the scheduler too:
``celery -A app.workers.celery_app beat -l info``.

Reliability settings: tasks are acknowledged *after* they finish
(``acks_late``) and re-queued if a worker dies, so every task must be idempotent.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import Settings, get_settings
from app.core.logging import configure_logging

TASK_MODULES = [
    "app.workers.document_tasks",
    "app.workers.embedding_tasks",
    "app.workers.maintenance_tasks",
]


def create_celery(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery(
        "school_in_a_box",
        broker=settings.broker_url,
        backend=settings.result_backend_url,
        include=TASK_MODULES,
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        result_expires=3600,
        task_time_limit=settings.content.task_soft_time_limit_seconds + 60,
        task_soft_time_limit=settings.content.task_soft_time_limit_seconds,
        # Must exceed the longest task so a running task is not redelivered to another worker.
        broker_transport_options={
            "visibility_timeout": settings.content.task_soft_time_limit_seconds * 3
        },
        broker_connection_retry_on_startup=True,
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=True,
        task_default_queue=settings.celery_queue,
        timezone="UTC",
        enable_utc=True,
        # Keep our JSON log format instead of Celery's.
        worker_hijack_root_logger=False,
        # Celery Beat (`celery -A app.workers.celery_app beat`): daily decay + plan refresh.
        beat_schedule={
            "daily-maintenance": {
                "task": "maintenance.daily",
                "schedule": crontab(
                    hour=settings.learning_engine.daily_maintenance_hour_utc, minute=0
                ),
            }
        },
    )
    return app


configure_logging(get_settings().log_level)
celery_app = create_celery()
