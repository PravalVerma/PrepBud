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


# --- Phase 3: documents, concepts, search ------------------------------------------------


@pytest.fixture
async def alice_library(
    client: httpx.AsyncClient, uploader: Any, processor: Any, fake_queue: Any
) -> dict[str, str]:
    from sqlalchemy import select

    from app.db.models import Concept
    from tests.fakes import concept_text
    from tests.integration.conftest import drain

    payload = await uploader.upload(
        ALICE, concept_text("Secret Alice Topic", "Only Alice may see this").encode()
    )
    await drain(processor, fake_queue)
    await uploader.upload(BOB, b"Concept: Bob Topic | Bob's own | end")
    await drain(processor, fake_queue)
    async with uploader.app.state.sessionmaker() as session:
        concept_id = await session.scalar(
            select(Concept.id).where(Concept.name == "Secret Alice Topic")
        )
    return {"document": payload["document_id"], "concept": str(concept_id)}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/documents/{document}"),
        ("DELETE", "/documents/{document}"),
        ("POST", "/documents/{document}/confirm-upload"),
        ("GET", "/concepts/{concept}"),
        ("GET", "/concepts/{concept}/graph"),
    ],
)
async def test_other_users_documents_and_concepts_are_404(
    client: httpx.AsyncClient, alice_library: dict[str, str], method: str, path: str
) -> None:
    url = API + path.format(**alice_library)
    resp = await client.request(method, url, headers=BOB)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert (
        await client.get(API + f"/documents/{alice_library['document']}", headers=ALICE)
    ).status_code == 200


async def test_lists_and_search_never_show_other_users_content(
    client: httpx.AsyncClient, alice_library: dict[str, str]
) -> None:
    docs = (await client.get(f"{API}/documents", headers=BOB)).json()["data"]
    concepts = (await client.get(f"{API}/concepts", headers=BOB)).json()["data"]
    assert [d["title"] for d in docs] == ["notes"]
    assert [c["name"] for c in concepts] == ["Bob Topic"]
    for mode in ("keyword", "semantic", "hybrid"):
        hits = (
            await client.get(
                f"{API}/search", params={"q": "Secret Alice Topic", "mode": mode}, headers=BOB
            )
        ).json()["data"]
        assert all(h["document_id"] != alice_library["document"] for h in hits), mode
    # Filtering by Alice's ids does not leak either.
    leaked = await client.get(
        f"{API}/concepts", params={"document_id": alice_library["document"]}, headers=BOB
    )
    assert leaked.json()["data"] == []
    leaked_search = await client.get(
        f"{API}/search",
        params={"q": "Alice", "document_id": alice_library["document"]},
        headers=BOB,
    )
    assert leaked_search.json()["data"] == []


async def test_upload_under_other_users_subject_is_404(
    client: httpx.AsyncClient, alice_tree: dict[str, str], uploader: Any
) -> None:
    resp = await uploader.request_url(BOB, subject_id=alice_tree["subject"])
    assert resp.status_code == 404
    resp = await uploader.request_url(BOB, course_id=alice_tree["course"])
    assert resp.status_code == 404
