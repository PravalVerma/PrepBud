"""AC-2.3 — accessing another user's resources returns 404 (never 403), and lists
only ever contain the caller's own data."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.support import auth

API = "/api/v1"
ALICE = auth("alice", "alice@example.com")
BOB = auth("bob", "bob@example.com")


@pytest.fixture
async def alice_tree(client: httpx.AsyncClient) -> dict[str, str]:
    async def create(path: str, name: str) -> str:
        resp = await client.post(f"{API}{path}", json={"name": name}, headers=ALICE)
        assert resp.status_code == 201
        return str(resp.json()["data"]["id"])

    subject = await create("/subjects", "Alice Maths")
    course = await create(f"/subjects/{subject}/courses", "Alice Course")
    chapter = await create(f"/courses/{course}/chapters", "Alice Chapter")
    section = await create(f"/chapters/{chapter}/sections", "Alice Section")
    # Bob exists and has data of his own.
    await client.post(f"{API}/subjects", json={"name": "Bob History"}, headers=BOB)
    return {"subject": subject, "course": course, "chapter": chapter, "section": section}


ITEM_PATHS = {
    "subject": "/subjects/{}",
    "course": "/courses/{}",
    "chapter": "/chapters/{}",
    "section": "/sections/{}",
}
CHILD_COLLECTIONS = {
    "subject": "/subjects/{}/courses",
    "course": "/courses/{}/chapters",
    "chapter": "/chapters/{}/sections",
}


@pytest.mark.parametrize("kind", list(ITEM_PATHS))
@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_other_users_item_is_404(
    client: httpx.AsyncClient, alice_tree: dict[str, str], kind: str, method: str
) -> None:
    url = API + ITEM_PATHS[kind].format(alice_tree[kind])
    body: dict[str, Any] | None = {"name": "hijacked"} if method == "PATCH" else None

    resp = await client.request(method, url, json=body, headers=BOB)

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    # Alice's resource is untouched.
    owner_view = await client.get(url, headers=ALICE)
    assert owner_view.status_code == 200
    assert owner_view.json()["data"]["name"].startswith("Alice")


@pytest.mark.parametrize("kind", list(CHILD_COLLECTIONS))
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_other_users_child_collection_is_404(
    client: httpx.AsyncClient, alice_tree: dict[str, str], kind: str, method: str
) -> None:
    url = API + CHILD_COLLECTIONS[kind].format(alice_tree[kind])
    body = {"name": "planted"} if method == "POST" else None

    resp = await client.request(method, url, json=body, headers=BOB)

    assert resp.status_code == 404
    owner_list = await client.get(url, headers=ALICE)
    assert all(n["name"] != "planted" for n in owner_list.json()["data"])


async def test_subject_list_contains_only_own_subjects(
    client: httpx.AsyncClient, alice_tree: dict[str, str]
) -> None:
    alice = await client.get(f"{API}/subjects", headers=ALICE)
    bob = await client.get(f"{API}/subjects", headers=BOB)

    assert [s["name"] for s in alice.json()["data"]] == ["Alice Maths"]
    assert [s["name"] for s in bob.json()["data"]] == ["Bob History"]
    assert bob.json()["meta"]["pagination"]["total"] == 1


async def test_profiles_are_per_user(client: httpx.AsyncClient) -> None:
    await client.patch(f"{API}/profile", json={"grade_level": "Alice grade"}, headers=ALICE)

    bob = await client.get(f"{API}/profile", headers=BOB)

    assert bob.json()["data"]["email"] == "bob@example.com"
    assert bob.json()["data"]["grade_level"] is None


async def test_missing_and_foreign_resources_are_indistinguishable(
    client: httpx.AsyncClient, alice_tree: dict[str, str]
) -> None:
    import uuid

    foreign = await client.get(f"{API}/subjects/{alice_tree['subject']}", headers=BOB)
    missing = await client.get(f"{API}/subjects/{uuid.uuid4()}", headers=BOB)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]
