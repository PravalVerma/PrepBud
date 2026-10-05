"""Daily maintenance (ARCHITECTURE §2 "Mastery decay — Celery Beat", LEARNING_ENGINE §5.2, §8.3).

``maintenance.daily`` runs once a day (Celery Beat, ``daily_maintenance_hour_utc``) and fans
out one ``maintenance.refresh_user`` per learner. Each refresh, in one transaction:

1. materialises mastery decay into ``mastery_level`` (AC-6.5) — see
   `MasteryTracker.materialise_decay`; exponential decay composes, so this never compounds,
2. marks open plan items whose day has passed as ``overdue``,
3. regenerates the study plan (forgotten concepts become reviews; the horizon rolls on).

Both tasks are idempotent: running them twice on the same day changes nothing further.

Run the scheduler with ``celery -A app.workers.celery_app beat -l info``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import LearningGoal, StudentConceptMastery, StudyPlan
from app.integrations.redis import create_redis
from app.services.student_model.mastery_tracker import MasteryTracker, cache_key
from app.services.study_plan.plan_service import StudyPlanService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

DAILY_MAINTENANCE = "maintenance.daily"
REFRESH_USER = "maintenance.refresh_user"


@asynccontextmanager
async def maintenance_runtime(
    settings: Settings,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Redis]]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = create_redis(settings)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False), redis
    finally:
        await redis.aclose()
        await engine.dispose()


async def learner_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Everyone with something to maintain: mastery records, an active plan or goal."""
    stmt = union(
        select(StudentConceptMastery.user_id),
        select(StudyPlan.user_id).where(StudyPlan.status == "active"),
        select(LearningGoal.user_id).where(LearningGoal.status == "active"),
    )
    return sorted({row[0] for row in (await session.execute(stmt)).all()}, key=str)


async def refresh_learner(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis | None,
    settings: Settings,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with sessionmaker() as session:
        tracker = MasteryTracker(session, settings.learning_engine, redis=redis)
        rows = (
            await session.scalars(
                select(StudentConceptMastery)
                .where(StudentConceptMastery.user_id == user_id)
                .with_for_update()
            )
        ).all()
        decayed = [r for r in rows if tracker.materialise_decay(r)]
        await session.flush()
        planner = StudyPlanService(session, settings, redis=redis)
        overdue = await planner.mark_overdue(user_id)
        await planner.regenerate(user_id, reason="daily")
        await session.commit()
    if redis is not None and decayed:
        try:
            await redis.delete(*(cache_key(user_id, r.concept_id) for r in decayed))
        except Exception as exc:  # cache entries expire on their own
            logger.warning("mastery cache invalidation failed", extra={"error": type(exc).__name__})
    return {"user_id": str(user_id), "decayed": len(decayed), "overdue": overdue}


async def run_refresh_user(user_id: str) -> dict[str, Any]:
    settings = get_settings()
    async with maintenance_runtime(settings) as (sessionmaker, redis):
        return await refresh_learner(sessionmaker, redis, settings, uuid.UUID(user_id))


async def run_daily() -> list[str]:
    settings = get_settings()
    async with maintenance_runtime(settings) as (sessionmaker, _redis), sessionmaker() as session:
        return [str(u) for u in await learner_ids(session)]


@celery_app.task(name=REFRESH_USER, max_retries=3, default_retry_delay=300)
def refresh_user(user_id: str) -> dict[str, Any]:
    return asyncio.run(run_refresh_user(user_id))


@celery_app.task(name=DAILY_MAINTENANCE)
def daily_maintenance() -> int:
    users = asyncio.run(run_daily())
    for user_id in users:
        refresh_user.delay(user_id)
    logger.info("daily maintenance scheduled", extra={"learners": len(users)})
    return len(users)
