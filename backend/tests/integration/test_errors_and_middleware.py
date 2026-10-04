"""Error envelope (API_CONTRACT §1–2), request IDs, security headers, CORS."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.core.middleware import SECURITY_HEADERS
from tests.support import auth


async def test_unknown_route_uses_error_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/does-not-exist")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert set(body["meta"]) == {"request_id", "timestamp"}


async def test_method_not_allowed(client: httpx.AsyncClient) -> None:
    resp = await client.put("/api/v1/subjects", headers=auth("m"))
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_unhandled_exception_returns_500_envelope(app: FastAPI) -> None:
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    app.add_api_route("/api/v1/_boom", boom)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/api/v1/_boom")

    assert resp.status_code == 500
    assert resp.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "An unexpected error occurred",
        "details": {},
    }
    assert "secret internal detail" not in resp.text


async def test_request_id_generated_and_propagated(client: httpx.AsyncClient) -> None:
    generated = await client.get("/health")
    echoed = await client.get("/health", headers={"X-Request-ID": "trace-abc_123"})
    sanitized = await client.get("/health", headers={"X-Request-ID": "bad id <script>"})

    assert len(generated.headers["X-Request-ID"]) == 32
    assert echoed.headers["X-Request-ID"] == "trace-abc_123"
    assert sanitized.headers["X-Request-ID"] != "bad id <script>"


async def test_request_id_matches_envelope_meta(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/profile", headers={**auth("rid"), "X-Request-ID": "rid-1"})
    assert resp.json()["meta"]["request_id"] == "rid-1"


async def test_security_headers_present(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    for name, value in SECURITY_HEADERS.items():
        assert resp.headers[name] == value


async def test_docs_skip_strict_csp(client: httpx.AsyncClient) -> None:
    resp = await client.get("/docs")
    assert resp.status_code == 200
    assert "Content-Security-Policy" not in resp.headers
    assert resp.headers["X-Frame-Options"] == "DENY"


async def test_cors_allows_only_frontend_origin(client: httpx.AsyncClient) -> None:
    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    }
    allowed = await client.options(
        "/api/v1/subjects", headers={"Origin": "http://localhost:3000", **preflight}
    )
    denied = await client.options(
        "/api/v1/subjects", headers={"Origin": "https://evil.example", **preflight}
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in denied.headers


async def test_openapi_schema_lists_contract_paths(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    for p in [
        "/api/v1/health",
        "/api/v1/auth/callback",
        "/api/v1/profile",
        "/api/v1/subjects",
        "/api/v1/subjects/{subject_id}/courses",
        "/api/v1/courses/{course_id}/chapters",
        "/api/v1/chapters/{chapter_id}/sections",
    ]:
        assert p in paths
