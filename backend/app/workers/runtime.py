"""Builds the document pipeline's dependencies inside a worker task.

Each Celery task runs its async body with ``asyncio.run`` (a fresh event loop), so
connections are created per task and closed afterwards; the database engine uses no
pool for the same reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.cost_tracker import AIUsageRecorder
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import get_prompt_manager
from app.config import Settings
from app.integrations.qdrant import SectionVectorStore
from app.integrations.redis import create_redis
from app.integrations.s3 import ObjectStorage
from app.services.content.document_processor import DocumentProcessor, TaskQueue
from app.services.content.text_extractor import TesseractOCR


class StorageNotConfiguredError(RuntimeError):
    pass


@asynccontextmanager
async def processing_runtime(
    settings: Settings, queue: TaskQueue | None = None
) -> AsyncIterator[DocumentProcessor]:
    storage = ObjectStorage.from_settings(settings)
    if storage is None:
        raise StorageNotConfiguredError("S3_BUCKET is not configured")
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    redis = create_redis(settings)
    vectors = SectionVectorStore.from_settings(settings)
    recorder = AIUsageRecorder(settings, sessionmaker, redis)
    llm = LLMClient(settings, recorder)
    try:
        yield DocumentProcessor(
            settings=settings,
            sessionmaker=sessionmaker,
            storage=storage,
            llm=llm,
            recorder=recorder,
            prompts=get_prompt_manager(),
            ocr=TesseractOCR(settings.content.ocr_language),
            vectors=vectors,
            redis=redis,
            queue=queue,
        )
    finally:
        await llm.aclose()
        if vectors is not None:
            await vectors.aclose()
        await redis.aclose()
        await engine.dispose()
