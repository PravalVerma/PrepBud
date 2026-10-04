"""`GET /health` — liveness + dependency status (API_CONTRACT §3.1). No auth.

Returns the contract's flat shape (not the data/meta envelope) so load balancers
and uptime monitors can consume it directly. The database is critical: if it is
unreachable the endpoint answers 503. Other dependencies degrade the status.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

ServiceStatus = Literal["ok", "error", "not_configured"]


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    version: str
    services: dict[str, ServiceStatus]


async def _check_database(request: Request, limit_s: float) -> ServiceStatus:
    async def probe() -> None:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(probe(), limit_s)
    except Exception as exc:
        logger.warning("health: database check failed", extra={"error": type(exc).__name__})
        return "error"
    return "ok"


async def _check_redis(request: Request, limit_s: float) -> ServiceStatus:
    try:
        await asyncio.wait_for(request.app.state.redis.ping(), limit_s)
    except Exception as exc:
        logger.warning("health: redis check failed", extra={"error": type(exc).__name__})
        return "error"
    return "ok"


async def _check_qdrant(settings: Settings, limit_s: float) -> ServiceStatus:
    if not settings.qdrant_url:
        return "not_configured"
    headers = {}
    if api_key := settings.qdrant_api_key.get_secret_value():
        headers["api-key"] = api_key
    try:
        async with httpx.AsyncClient(timeout=limit_s) as client:
            resp = await client.get(f"{settings.qdrant_url.rstrip('/')}/healthz", headers=headers)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("health: qdrant check failed", extra={"error": type(exc).__name__})
        return "error"
    return "ok"


@router.get("/health", response_model=HealthResponse, summary="Service health")
async def health(request: Request, response: Response) -> HealthResponse:
    settings: Settings = request.app.state.settings
    timeout = settings.health_check_timeout_seconds
    database, redis, qdrant = await asyncio.gather(
        _check_database(request, timeout),
        _check_redis(request, timeout),
        _check_qdrant(settings, timeout),
    )
    services: dict[str, ServiceStatus] = {"database": database, "redis": redis, "qdrant": qdrant}

    if database != "ok":
        status: Literal["healthy", "degraded", "unhealthy"] = "unhealthy"
        response.status_code = 503
    elif any(s == "error" for s in services.values()):
        status = "degraded"
    else:
        status = "healthy"
    return HealthResponse(status=status, version=settings.app_version, services=services)
