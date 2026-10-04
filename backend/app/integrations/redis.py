"""Redis client factory (cache, rate limiting; Celery broker in later phases)."""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import Settings


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.health_check_timeout_seconds,
        socket_timeout=settings.health_check_timeout_seconds,
    )
