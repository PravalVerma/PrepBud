"""Re-index a document's sections into Qdrant after an embedding / vector-store failure."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.config import get_settings
from app.services.content.document_processor import Outcome
from app.workers import runtime
from app.workers.celery_app import celery_app
from app.workers.document_tasks import retry_countdown
from app.workers.queue import REINDEX_DOCUMENT

MAX_RETRIES = 5


async def run_reindex_document(document_id: str, user_id: str, final_attempt: bool) -> Outcome:
    async with runtime.processing_runtime(get_settings()) as processor:
        return await processor.reindex(
            uuid.UUID(document_id), uuid.UUID(user_id), final_attempt=final_attempt
        )


@celery_app.task(bind=True, name=REINDEX_DOCUMENT, max_retries=MAX_RETRIES)
def reindex_document(self: Any, document_id: str, user_id: str) -> str:
    final_attempt = self.request.retries >= self.max_retries
    outcome = asyncio.run(run_reindex_document(document_id, user_id, final_attempt))
    if outcome is Outcome.RETRY:
        raise self.retry(countdown=retry_countdown(self.request.retries + 1))
    return outcome.value
