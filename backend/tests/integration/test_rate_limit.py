"""Per-user Redis rate limiting (SECURITY_MODEL §5.1, API_CONTRACT §6)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

from tests.support import auth


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"rate_limit_per_minute": 3, "rate_limit_auth_per_minute": 2}


async def test_headers_count_down_then_429(client: httpx.AsyncClient) -> None:
    headers = auth("limited")
    seen = []
    for _ in range(3):
        resp = await client.get("/api/v1/subjects", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["X-RateLimit-Limit"] == "3"
        seen.append(int(resp.headers["X-RateLimit-Remaining"]))
        assert int(resp.headers["X-RateLimit-Reset"]) > 0

    blocked = await client.get("/api/v1/subjects", headers=headers)

    assert seen == [2, 1, 0]
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert blocked.headers["Retry-After"] == "60"


async def test_limits_are_per_user(client: httpx.AsyncClient) -> None:
    for _ in range(4):
        await client.get("/api/v1/profile", headers=auth("noisy"))

    resp = await client.get("/api/v1/profile", headers=auth("quiet"))

    assert resp.status_code == 200


async def test_auth_callback_has_its_own_stricter_limit(client: httpx.AsyncClient) -> None:
    headers = auth("cb-limited")
    codes = [
        (await client.post("/api/v1/auth/callback", json={}, headers=headers)).status_code
        for _ in range(3)
    ]
    assert codes == [201, 200, 429]


async def test_health_is_not_rate_limited(client: httpx.AsyncClient) -> None:
    codes = {(await client.get("/health")).status_code for _ in range(5)}
    assert codes == {200}


async def test_redis_outage_fails_open(app: FastAPI, client: httpx.AsyncClient) -> None:
    real = app.state.redis
    app.state.redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    try:
        resp = await client.get("/api/v1/profile", headers=auth("during-outage"))
    finally:
        await app.state.redis.aclose()
        app.state.redis = real

    assert resp.status_code == 200
    assert "X-RateLimit-Limit" not in resp.headers


async def test_disabled_rate_limit_sends_no_headers(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state.settings, "rate_limit_enabled", False)
    for _ in range(5):
        resp = await client.get("/api/v1/profile", headers=auth("unlimited"))
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" not in resp.headers
