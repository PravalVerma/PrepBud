"""AC-2.6 — GET /health reports service status including database connectivity."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import health as health_module


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
async def test_health_reports_all_services(client: httpx.AsyncClient, path: str) -> None:
    resp = await client.get(path)

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "healthy",
        "version": "0.1.0",
        "services": {"database": "ok", "redis": "ok", "qdrant": "not_configured"},
    }


async def test_health_requires_no_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/health", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 200


async def test_database_down_returns_503_unhealthy(app: FastAPI, client: httpx.AsyncClient) -> None:
    real_engine = app.state.engine
    app.state.engine = create_async_engine(
        "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none", connect_args={"timeout": 1}
    )
    try:
        resp = await client.get("/health")
    finally:
        await app.state.engine.dispose()
        app.state.engine = real_engine

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["services"]["database"] == "error"


async def test_redis_down_is_degraded(app: FastAPI, client: httpx.AsyncClient) -> None:
    real_redis = app.state.redis
    app.state.redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    try:
        resp = await client.get("/health")
    finally:
        await app.state.redis.aclose()
        app.state.redis = real_redis

    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
    assert resp.json()["services"]["redis"] == "error"


class _FakeQdrantClient:
    status = 200

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeQdrantClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
        assert url.endswith("/healthz")
        assert headers == {"api-key": "qdrant-key"}
        return httpx.Response(self.status, request=httpx.Request("GET", url))


@pytest.mark.parametrize(("status", "expected"), [(200, "ok"), (500, "error")])
async def test_qdrant_check_when_configured(
    app: FastAPI,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: str,
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(app.state.settings, "qdrant_url", "https://qdrant.example")
    monkeypatch.setattr(app.state.settings, "qdrant_api_key", SecretStr("qdrant-key"))
    monkeypatch.setattr(_FakeQdrantClient, "status", status)
    monkeypatch.setattr(health_module.httpx, "AsyncClient", _FakeQdrantClient)

    resp = await client.get("/health")

    assert resp.json()["services"]["qdrant"] == expected
    assert resp.json()["status"] == ("healthy" if expected == "ok" else "degraded")
