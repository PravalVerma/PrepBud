"""Enqueueing background work from the API (the API never imports task bodies)."""

from __future__ import annotations

import asyncio
import uuid

from celery import Celery

PROCESS_DOCUMENT = "documents.process"
REINDEX_DOCUMENT = "documents.reindex"


class CeleryTaskQueue:
    def __init__(self, app: Celery) -> None:
        self.app = app

    async def _send(
        self,
        name: str,
        args: list[str],
        countdown: int | None = None,
        task_id: str | None = None,
    ) -> str:
        task_id = task_id or str(uuid.uuid4())
        await asyncio.to_thread(
            self.app.send_task, name, args=args, task_id=task_id, countdown=countdown
        )
        return task_id

    async def enqueue_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID, *, task_id: str | None = None
    ) -> str:
        return await self._send(PROCESS_DOCUMENT, [str(document_id), str(user_id)], task_id=task_id)

    async def enqueue_reindex(
        self, document_id: uuid.UUID, user_id: uuid.UUID, *, countdown: int = 60
    ) -> str:
        return await self._send(REINDEX_DOCUMENT, [str(document_id), str(user_id)], countdown)
