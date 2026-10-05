"""Document processing task (ARCHITECTURE §3.1). Safe to retry — see
`app.services.content.document_processor` for the idempotency guarantees."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.config import get_settings
from app.services.content.document_processor import Outcome
from app.workers import runtime
from app.workers.celery_app import celery_app
from app.workers.queue import PROCESS_DOCUMENT, CeleryTaskQueue

MAX_RETRIES = get_settings().content.task_max_retries


def retry_countdown(retries: int) -> int:
    """Exponential backoff: 30 s, 60 s, 120 s … capped at 5 minutes."""
    return int(min(300, 30 * 2**retries))


async def run_process_document(
    document_id: str, user_id: str, task_id: str | None, final_attempt: bool
) -> Outcome:
    settings = get_settings()
    async with runtime.processing_runtime(settings, CeleryTaskQueue(celery_app)) as processor:
        return await processor.process(
            uuid.UUID(document_id), uuid.UUID(user_id), task_id=task_id, final_attempt=final_attempt
        )


@celery_app.task(bind=True, name=PROCESS_DOCUMENT, max_retries=MAX_RETRIES)
def process_document(self: Any, document_id: str, user_id: str) -> str:
    final_attempt = self.request.retries >= self.max_retries
    outcome = asyncio.run(
        run_process_document(document_id, user_id, self.request.id, final_attempt)
    )
    if outcome is Outcome.RETRY:
        raise self.retry(countdown=retry_countdown(self.request.retries))
    return outcome.value
