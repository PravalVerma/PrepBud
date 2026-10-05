"""AC-2.1 (user + profile auto-created on first call) and AC-2.4 (no valid JWT → 401)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StudentProfile, User
from tests.support import (
    OTHER_EC_PRIVATE_KEY,
    TEST_JWT_SECRET,
    auth,
    make_token,
)

PROTECTED = "/api/v1/profile"


async def _count(db: AsyncSession, model: type[User] | type[StudentProfile]) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


# --- AC-2.4: requests without a valid JWT ------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    ("label", "headers"),
    [
        ("missing header", {}),
        ("empty bearer", {"Authorization": "Bearer "}),
        ("basic scheme", {"Authorization": "Basic dXNlcjpwYXNz"}),
        ("not a jwt", _bearer("not-a-jwt")),
        ("expired", _bearer(make_token(exp_delta=-3600))),
        ("wrong audience", _bearer(make_token(aud="anon"))),
        ("missing audience", _bearer(make_token(aud=None))),
        ("wrong issuer", _bearer(make_token(iss="https://evil.example/auth/v1"))),
        ("bad signature", _bearer(make_token(secret="x" * 40))),
        (
            "es256 signed by unknown key",
            _bearer(make_token(alg="ES256", private_key=OTHER_EC_PRIVATE_KEY)),
        ),
        ("es256 unknown kid", _bearer(make_token(alg="ES256", kid="rotated-away"))),
        ("es256 without kid", _bearer(make_token(alg="ES256", kid=None))),
    ],
)
async def test_invalid_credentials_return_401(
    client: httpx.AsyncClient, db: AsyncSession, label: str, headers: dict[str, str]
) -> None:
    resp = await client.get(PROTECTED, headers=headers)

    assert resp.status_code == 401, label
    body = resp.json()
    assert body["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert body["meta"]["request_id"]
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    assert await _count(db, User) == 0  # nothing provisioned on failure


async def test_alg_none_token_is_rejected(client: httpx.AsyncClient) -> None:
    import jwt

    from tests.support import make_claims

    token = jwt.encode(make_claims(), key=None, algorithm="none")
    resp = await client.get(PROTECTED, headers=_bearer(token))
    assert resp.status_code == 401


async def test_every_protected_endpoint_requires_auth(client: httpx.AsyncClient) -> None:
    some_id = "00000000-0000-0000-0000-000000000001"
    calls = [
        ("GET", "/api/v1/profile"),
        ("PATCH", "/api/v1/profile"),
        ("GET", "/api/v1/subjects"),
        ("POST", "/api/v1/subjects"),
        ("GET", f"/api/v1/subjects/{some_id}"),
        ("GET", f"/api/v1/subjects/{some_id}/courses"),
        ("GET", f"/api/v1/courses/{some_id}/chapters"),
        ("GET", f"/api/v1/chapters/{some_id}/sections"),
        ("DELETE", f"/api/v1/sections/{some_id}"),
        ("POST", "/api/v1/auth/callback"),
        ("POST", "/api/v1/documents/upload-url"),
        ("POST", f"/api/v1/documents/{some_id}/confirm-upload"),
        ("GET", "/api/v1/documents"),
        ("GET", f"/api/v1/documents/{some_id}"),
        ("DELETE", f"/api/v1/documents/{some_id}"),
        ("GET", "/api/v1/concepts"),
        ("GET", f"/api/v1/concepts/{some_id}"),
        ("GET", f"/api/v1/concepts/{some_id}/graph"),
        ("GET", "/api/v1/search?q=x"),
        ("POST", "/api/v1/sessions"),
        ("GET", "/api/v1/sessions"),
        ("GET", f"/api/v1/sessions/{some_id}"),
        ("POST", f"/api/v1/sessions/{some_id}/messages"),
        ("POST", f"/api/v1/sessions/{some_id}/end"),
        ("POST", f"/api/v1/sessions/{some_id}/pause"),
        ("POST", f"/api/v1/sessions/{some_id}/resume"),
        ("POST", f"/api/v1/sessions/{some_id}/ws-ticket"),
    ]
    for method, path in calls:
        resp = await client.request(method, path, json={})
        assert resp.status_code == 401, f"{method} {path}"


# --- AC-2.1: first-call provisioning ---------------------------------------------------------


async def test_first_api_call_creates_user_and_profile(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    headers = auth(
        "supabase-uid-1",
        "First.Login@Example.com",
        user_metadata={"full_name": "Jane Doe", "avatar_url": "https://img/x.png"},
    )

    resp = await client.get(PROTECTED, headers=headers)

    assert resp.status_code == 200
    user = await db.scalar(select(User).where(User.auth_id == "supabase-uid-1"))
    assert user is not None
    assert user.email == "first.login@example.com"
    assert user.display_name == "Jane Doe"
    assert user.avatar_url == "https://img/x.png"
    assert user.is_active is True
    profile = await db.scalar(select(StudentProfile).where(StudentProfile.user_id == user.id))
    assert profile is not None
    assert profile.onboarding_state == "new"
    assert resp.json()["data"]["id"] == str(profile.id)


async def test_repeat_calls_do_not_duplicate_user(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    headers = auth("repeat-user")
    for _ in range(3):
        assert (await client.get(PROTECTED, headers=headers)).status_code == 200

    assert await _count(db, User) == 1
    assert await _count(db, StudentProfile) == 1


async def test_concurrent_first_requests_create_exactly_one_user(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    headers = auth("racing-user")

    responses = await asyncio.gather(*(client.get(PROTECTED, headers=headers) for _ in range(8)))

    assert {r.status_code for r in responses} == {200}
    assert await _count(db, User) == 1
    assert await _count(db, StudentProfile) == 1


async def test_es256_token_verified_via_jwks(client: httpx.AsyncClient, db: AsyncSession) -> None:
    token = make_token("es-user", "es@example.com", alg="ES256")

    resp = await client.get(PROTECTED, headers=_bearer(token))

    assert resp.status_code == 200
    assert await db.scalar(select(User.email).where(User.auth_id == "es-user")) == "es@example.com"


async def test_token_without_email_claim_is_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get(PROTECTED, headers=_bearer(make_token("no-email", email=None)))
    assert resp.status_code == 401
    assert "email" in resp.json()["error"]["message"]


async def test_email_bound_to_other_identity_returns_409(client: httpx.AsyncClient) -> None:
    assert (
        await client.get(PROTECTED, headers=auth("id-one", "shared@example.com"))
    ).status_code == 200

    resp = await client.get(PROTECTED, headers=auth("id-two", "shared@example.com"))

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_deactivated_user_gets_403(client: httpx.AsyncClient, db: AsyncSession) -> None:
    headers = auth("soon-disabled")
    await client.get(PROTECTED, headers=headers)
    await db.execute(update(User).where(User.auth_id == "soon-disabled").values(is_active=False))
    await db.commit()

    resp = await client.get(PROTECTED, headers=headers)

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


# --- POST /auth/callback ------------------------------------------------------------------


async def test_auth_callback_creates_then_is_idempotent(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    headers = auth("callback-user", "cb@example.com")
    body = {"auth_id": "callback-user", "email": "cb@example.com", "display_name": "CB Student"}

    first = await client.post("/api/v1/auth/callback", json=body, headers=headers)
    second = await client.post("/api/v1/auth/callback", json=body, headers=headers)

    assert first.status_code == 201
    data = first.json()["data"]
    assert data["email"] == "cb@example.com"
    assert data["display_name"] == "CB Student"
    assert data["onboarding_state"] == "new"
    assert second.status_code == 200
    assert second.json()["data"]["id"] == data["id"]
    assert await _count(db, User) == 1


async def test_auth_callback_ignores_identity_in_body(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    """The body cannot provision a record for someone else's identity."""
    headers = auth("real-sub", "real@example.com")
    body = {"auth_id": "victim-sub", "email": "victim@example.com"}

    resp = await client.post("/api/v1/auth/callback", json=body, headers=headers)

    assert resp.status_code == 201
    assert resp.json()["data"]["email"] == "real@example.com"
    assert await db.scalar(select(User).where(User.auth_id == "victim-sub")) is None


async def test_auth_callback_validates_body(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/callback", json={"email": "not-an-email"}, headers=auth("v")
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_hs256_rejected_when_secret_not_configured(app, client: httpx.AsyncClient) -> None:
    from app.core.security import JWTVerifier
    from tests.support import build_settings

    jwks_cache = app.state.jwt_verifier.jwks_cache
    app.state.jwt_verifier = JWTVerifier(
        build_settings(supabase_jwt_secret=""), jwks_cache=jwks_cache
    )

    hs = await client.get(PROTECTED, headers=_bearer(make_token(secret=TEST_JWT_SECRET)))
    es = await client.get(PROTECTED, headers=_bearer(make_token("es-only", alg="ES256")))

    assert hs.status_code == 401
    assert es.status_code == 200
