"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.cost_tracker import AIUsageRecorder
from app.ai.llm_client import LLMClient
from app.api import api_router, health
from app.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import register_middleware
from app.core.security import JWTVerifier
from app.db.base import create_engine, create_sessionmaker, warm_pool
from app.integrations.qdrant import SectionVectorStore
from app.integrations.redis import create_redis
from app.integrations.s3 import ObjectStorage
from app.workers.celery_app import create_celery
from app.workers.queue import CeleryTaskQueue

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    await warm_pool(engine, settings.database_pool_size)
    app.state.redis = create_redis(settings)
    app.state.jwt_verifier = JWTVerifier(settings)
    app.state.storage = ObjectStorage.from_settings(settings)
    app.state.vector_store = SectionVectorStore.from_settings(settings)
    app.state.ai_recorder = AIUsageRecorder(settings, app.state.sessionmaker, app.state.redis)
    app.state.llm = LLMClient(settings, app.state.ai_recorder)
    app.state.task_queue = CeleryTaskQueue(create_celery(settings))
    if not settings.supabase_jwt_secret.get_secret_value() and not settings.jwks_url:
        logger.warning("No Supabase JWT secret or JWKS URL configured; all auth will fail")
    logger.info("startup complete", extra={"env": settings.app_env})
    try:
        yield
    finally:
        await app.state.llm.aclose()
        if app.state.vector_store is not None:
            await app.state.vector_store.aclose()
        await app.state.redis.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="AI-powered adaptive learning platform — REST API (see docs/API_CONTRACT.md).",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings

    register_exception_handlers(app)
    register_middleware(app, settings)
    app.include_router(api_router, prefix=settings.api_prefix)
    # Unversioned alias for load balancers / uptime checks.
    app.include_router(health.router, include_in_schema=False)
    return app


app = create_app()
