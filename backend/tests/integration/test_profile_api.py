"""GET/PATCH /profile (AC-2.2)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StudentProfile, User
from tests.support import auth

URL = "/api/v1/profile"


async def test_get_profile_returns_defaults(client: httpx.AsyncClient) -> None:
    resp = await client.get(URL, headers=auth("p-user", "p@example.com"))

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "meta"}
    assert set(body["meta"]) == {"request_id", "timestamp"}
    data = body["data"]
    assert data["email"] == "p@example.com"
    assert data["difficulty_band"] == "intermediate"
    assert data["language"] == "en"
    assert data["timezone"] == "UTC"
    assert data["preferences"] == {}
    assert data["cumulative_stats"] == {}
    assert data["onboarding_state"] == "new"


async def test_patch_profile_updates_fields(client: httpx.AsyncClient, db: AsyncSession) -> None:
    headers = auth("patcher")
    before = (await client.get(URL, headers=headers)).json()["data"]

    resp = await client.patch(
        URL,
        headers=headers,
        json={
            "grade_level": "11th grade",
            "difficulty_band": "advanced",
            "timezone": "Asia/Kolkata",
            "language": "en-GB",
            "display_name": "  Priya  ",
            "preferences": {"theme": "dark"},
            "onboarding_state": "profile_set",
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == before["id"]
    assert data["grade_level"] == "11th grade"
    assert data["difficulty_band"] == "advanced"
    assert data["timezone"] == "Asia/Kolkata"
    assert data["language"] == "en-GB"
    assert data["display_name"] == "Priya"
    assert data["preferences"] == {"theme": "dark"}
    assert data["onboarding_state"] == "profile_set"

    # Persisted, and the updated_at trigger fired.
    profile = await db.scalar(select(StudentProfile))
    assert profile is not None
    assert profile.timezone == "Asia/Kolkata"
    assert profile.updated_at is not None
    assert profile.created_at is not None
    assert profile.updated_at > profile.created_at
    assert await db.scalar(select(User.display_name)) == "Priya"


async def test_patch_is_partial(client: httpx.AsyncClient) -> None:
    headers = auth("partial")
    await client.patch(URL, headers=headers, json={"grade_level": "9th grade"})

    resp = await client.patch(URL, headers=headers, json={"timezone": "Europe/London"})

    data = resp.json()["data"]
    assert data["grade_level"] == "9th grade"
    assert data["timezone"] == "Europe/London"


async def test_grade_level_can_be_cleared(client: httpx.AsyncClient) -> None:
    headers = auth("clearer")
    await client.patch(URL, headers=headers, json={"grade_level": "10th"})

    resp = await client.patch(URL, headers=headers, json={"grade_level": None})

    assert resp.json()["data"]["grade_level"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"timezone": "Mars/Olympus_Mons"},
        {"difficulty_band": "expert"},
        {"difficulty_band": None},
        {"language": "English"},
        {"onboarding_state": "done"},
        {"unknown_field": 1},
        {"preferences": {f"k{i}": i for i in range(51)}},
        {"grade_level": "x" * 101},
    ],
)
async def test_patch_profile_rejects_invalid_input(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    resp = await client.patch(URL, headers=auth("validator"), json=body)

    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["errors"]


async def test_malformed_json_is_400(client: httpx.AsyncClient) -> None:
    resp = await client.patch(
        URL,
        headers={**auth("json"), "Content-Type": "application/json"},
        content=b"{not json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
