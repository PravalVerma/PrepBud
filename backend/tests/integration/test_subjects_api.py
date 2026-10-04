"""Curriculum CRUD: subjects, courses, chapters, sections (AC-2.2)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Concept, Course, StudentConceptMastery, User
from tests.support import auth

API = "/api/v1"
A = auth("owner-a")


async def _create(client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post(f"{API}{path}", json=body, headers=A)
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


async def _hierarchy(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    subject = await _create(client, "/subjects", {"name": "Mathematics", "icon": "📐"})
    course = await _create(client, f"/subjects/{subject['id']}/courses", {"name": "Algebra II"})
    chapter = await _create(client, f"/courses/{course['id']}/chapters", {"name": "Quadratics"})
    section = await _create(client, f"/chapters/{chapter['id']}/sections", {"name": "Formula"})
    return {"subject": subject, "course": course, "chapter": chapter, "section": section}


# --- Subjects ----------------------------------------------------------------------------


async def test_create_subject_returns_201_with_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        f"{API}/subjects",
        json={"name": "  Mathematics ", "description": "High school mathematics", "icon": "📐"},
        headers=A,
    )

    assert resp.status_code == 201
    body = resp.json()
    data = body["data"]
    uuid.UUID(data["id"])
    assert data["name"] == "Mathematics"
    assert data["description"] == "High school mathematics"
    assert data["icon"] == "📐"
    assert data["course_count"] == 0
    assert data["concept_count"] == 0
    assert data["avg_mastery"] == 0.0
    assert data["created_at"]
    assert data["updated_at"]
    assert body["meta"]["request_id"] == resp.headers["X-Request-ID"]


async def test_list_subjects_is_paginated(client: httpx.AsyncClient) -> None:
    for i in range(25):
        await _create(client, "/subjects", {"name": f"Subject {i:02d}"})

    first = await client.get(f"{API}/subjects", headers=A)
    page3 = await client.get(f"{API}/subjects?page=3&per_page=10", headers=A)
    beyond = await client.get(f"{API}/subjects?page=9&per_page=10", headers=A)

    assert first.json()["meta"]["pagination"] == {
        "total": 25,
        "page": 1,
        "per_page": 20,
        "total_pages": 2,
    }
    assert len(first.json()["data"]) == 20
    assert [s["name"] for s in page3.json()["data"]] == [f"Subject {i}" for i in range(20, 25)]
    assert page3.json()["meta"]["pagination"]["total_pages"] == 3
    assert beyond.json()["data"] == []


async def test_empty_list_pagination(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{API}/subjects", headers=A)
    assert resp.json()["data"] == []
    assert resp.json()["meta"]["pagination"]["total_pages"] == 0


@pytest.mark.parametrize("query", ["per_page=101", "per_page=0", "page=0", "page=abc"])
async def test_invalid_pagination_is_400(client: httpx.AsyncClient, query: str) -> None:
    resp = await client.get(f"{API}/subjects?{query}", headers=A)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 201},
        {"name": "bad\x00name"},
        {"name": "ok", "icon": ""},
        {"name": "ok", "user_id": str(uuid.uuid4())},
    ],
)
async def test_create_subject_validation(client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    resp = await client.post(f"{API}/subjects", json=body, headers=A)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_duplicate_subject_name_is_409(client: httpx.AsyncClient) -> None:
    await _create(client, "/subjects", {"name": "Physics"})

    resp = await client.post(f"{API}/subjects", json={"name": "Physics"}, headers=A)

    assert resp.status_code == 409
    assert resp.json()["error"] == {
        "code": "CONFLICT",
        "message": "A subject with this name already exists",
        "details": {"field": "name"},
    }


async def test_same_subject_name_allowed_for_different_users(client: httpx.AsyncClient) -> None:
    await _create(client, "/subjects", {"name": "Physics"})
    resp = await client.post(f"{API}/subjects", json={"name": "Physics"}, headers=auth("other"))
    assert resp.status_code == 201


async def test_get_update_delete_subject(client: httpx.AsyncClient) -> None:
    subject = await _create(client, "/subjects", {"name": "Chem", "description": "old"})
    url = f"{API}/subjects/{subject['id']}"

    got = await client.get(url, headers=A)
    patched = await client.patch(url, json={"name": "Chemistry", "description": None}, headers=A)
    deleted = await client.delete(url, headers=A)
    after = await client.get(url, headers=A)

    assert got.status_code == 200
    assert got.json()["data"]["name"] == "Chem"
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "Chemistry"
    assert patched.json()["data"]["description"] is None
    assert patched.json()["data"]["updated_at"] >= got.json()["data"]["updated_at"]
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert after.status_code == 404
    assert after.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


async def test_rename_subject_to_existing_name_is_409(client: httpx.AsyncClient) -> None:
    await _create(client, "/subjects", {"name": "Biology"})
    other = await _create(client, "/subjects", {"name": "Botany"})

    resp = await client.patch(f"{API}/subjects/{other['id']}", json={"name": "Biology"}, headers=A)

    assert resp.status_code == 409


async def test_patch_subject_rejects_null_name(client: httpx.AsyncClient) -> None:
    subject = await _create(client, "/subjects", {"name": "Art"})
    resp = await client.patch(f"{API}/subjects/{subject['id']}", json={"name": None}, headers=A)
    assert resp.status_code == 400


async def test_subject_stats(client: httpx.AsyncClient, db: AsyncSession) -> None:
    h = await _hierarchy(client)
    subject_id = uuid.UUID(h["subject"]["id"])
    await _create(client, f"/subjects/{subject_id}/courses", {"name": "Geometry"})
    user_id = await db.scalar(select(User.id).where(User.auth_id == "owner-a"))
    assert user_id is not None
    concepts = [Concept(user_id=user_id, subject_id=subject_id, name=f"C{i}") for i in range(4)]
    db.add_all(concepts)
    await db.flush()
    db.add_all(
        [
            StudentConceptMastery(user_id=user_id, concept_id=concepts[0].id, mastery_level=0.8),
            StudentConceptMastery(user_id=user_id, concept_id=concepts[1].id, mastery_level=0.4),
        ]
    )
    await db.commit()

    listed = (await client.get(f"{API}/subjects", headers=A)).json()["data"][0]
    single = (await client.get(f"{API}/subjects/{subject_id}", headers=A)).json()["data"]

    for data in (listed, single):
        assert data["course_count"] == 2
        assert data["concept_count"] == 4
        assert data["avg_mastery"] == pytest.approx(0.3)  # (0.8 + 0.4 + 0 + 0) / 4


async def test_delete_subject_cascades_to_hierarchy(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    h = await _hierarchy(client)

    await client.delete(f"{API}/subjects/{h['subject']['id']}", headers=A)

    assert await db.scalar(select(func.count()).select_from(Course)) == 0
    assert (await client.get(f"{API}/sections/{h['section']['id']}", headers=A)).status_code == 404


# --- Courses / Chapters / Sections --------------------------------------------------------

NODES = [
    # (kind, parent kind, collection path template, item path template, parent fk)
    ("course", "subject", "/subjects/{}/courses", "/courses/{}", "subject_id"),
    ("chapter", "course", "/courses/{}/chapters", "/chapters/{}", "course_id"),
    ("section", "chapter", "/chapters/{}/sections", "/sections/{}", "chapter_id"),
]


@pytest.mark.parametrize(("kind", "parent", "collection", "item", "fk"), NODES)
async def test_node_crud(
    client: httpx.AsyncClient,
    kind: str,
    parent: str,
    collection: str,
    item: str,
    fk: str,
) -> None:
    h = await _hierarchy(client)
    parent_id = h[parent]["id"]
    coll_url = API + collection.format(parent_id)

    created = await client.post(
        coll_url, json={"name": f"New {kind}", "description": "d", "sort_order": 5}, headers=A
    )
    assert created.status_code == 201
    node = created.json()["data"]
    assert node[fk] == parent_id
    assert node["sort_order"] == 5
    item_url = API + item.format(node["id"])

    got = await client.get(item_url, headers=A)
    assert got.status_code == 200
    assert got.json()["data"]["name"] == f"New {kind}"

    patched = await client.patch(item_url, json={"name": "Renamed", "sort_order": 0}, headers=A)
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "Renamed"
    assert patched.json()["data"]["description"] == "d"

    listed = await client.get(coll_url, headers=A)
    assert listed.status_code == 200
    names = [n["name"] for n in listed.json()["data"]]
    assert names == [h[kind]["name"], "Renamed"]  # sort_order 0, then created_at
    assert listed.json()["meta"]["pagination"]["total"] == 2

    assert (await client.delete(item_url, headers=A)).status_code == 204
    assert (await client.get(item_url, headers=A)).status_code == 404


async def test_nodes_ordered_by_sort_order(client: httpx.AsyncClient) -> None:
    subject = await _create(client, "/subjects", {"name": "Ordering"})
    url = f"/subjects/{subject['id']}/courses"
    for name, order in [("third", 30), ("first", 10), ("second", 20)]:
        await _create(client, url, {"name": name, "sort_order": order})

    resp = await client.get(API + url, headers=A)

    assert [c["name"] for c in resp.json()["data"]] == ["first", "second", "third"]


@pytest.mark.parametrize(
    "path",
    [
        "/subjects/{}/courses",
        "/courses/{}/chapters",
        "/chapters/{}/sections",
    ],
)
async def test_create_under_missing_parent_is_404(client: httpx.AsyncClient, path: str) -> None:
    resp = await client.post(API + path.format(uuid.uuid4()), json={"name": "x"}, headers=A)
    assert resp.status_code == 404


async def test_negative_sort_order_rejected(client: httpx.AsyncClient) -> None:
    subject = await _create(client, "/subjects", {"name": "S"})
    resp = await client.post(
        f"{API}/subjects/{subject['id']}/courses", json={"name": "c", "sort_order": -1}, headers=A
    )
    assert resp.status_code == 400


async def test_invalid_uuid_path_is_400(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{API}/subjects/not-a-uuid", headers=A)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
