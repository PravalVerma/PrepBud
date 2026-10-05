"""Concept endpoints (API_CONTRACT §3.6, AC-3.2, AC-3.3)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Concept,
    ConceptRelationship,
    Misconception,
    StudentConceptMastery,
    StudentMisconception,
    User,
)
from app.services.content.document_processor import DocumentProcessor
from tests.fakes import FakeQueue, concept_text
from tests.integration.conftest import Uploader, drain
from tests.support import auth

API = "/api/v1"
A = auth("concept-owner")
CHAIN = "\n\n".join(
    [
        concept_text("Limits", "Value a function approaches", filler=30),
        concept_text("Derivatives", "Instantaneous rate of change", requires="Limits", filler=30),
        concept_text(
            "Chain Rule", "Derivative of a composition", requires="Derivatives", filler=30
        ),
        concept_text(
            "Power Rule", "Derivative of x^n is n x^(n-1)", requires="Derivatives", filler=30
        ),
    ]
).encode()


@pytest.fixture
async def concepts(
    uploader: Uploader, processor: DocumentProcessor, fake_queue: FakeQueue, db: AsyncSession
) -> dict[str, Concept]:
    await uploader.upload(A, CHAIN, filename="calculus.txt")
    assert await drain(processor, fake_queue) == ["ready"]
    return {c.name: c for c in (await db.scalars(select(Concept))).all()}


async def get(client: httpx.AsyncClient, path: str, **params: Any) -> httpx.Response:
    return await client.get(f"{API}{path}", params=params, headers=A)


class TestList:
    async def test_lists_extracted_concepts(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept]
    ) -> None:
        """AC-3.2: extracted concepts appear with names and descriptions."""
        resp = await get(client, "/concepts")
        body = resp.json()
        assert resp.status_code == 200
        assert [c["name"] for c in body["data"]] == [
            "Chain Rule",
            "Derivatives",
            "Limits",
            "Power Rule",
        ]
        derivatives = next(c for c in body["data"] if c["name"] == "Derivatives")
        assert derivatives["description"] == "Instantaneous rate of change"
        assert derivatives["prerequisite_count"] == 1
        assert derivatives["document_count"] == 1
        assert derivatives["difficulty_estimate"] == 0.5
        assert derivatives["mastery"] == {
            "level": 0.0,
            "label": "novice",
            "last_assessed_at": None,
            "next_review_at": None,
        }
        assert body["meta"]["pagination"]["total"] == 4

    async def test_search_full_text_and_partial(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept]
    ) -> None:
        by_description = await get(client, "/concepts", search="composition")
        assert [c["name"] for c in by_description.json()["data"]] == ["Chain Rule"]
        stemmed = await get(client, "/concepts", search="derivative")
        assert {c["name"] for c in stemmed.json()["data"]} >= {
            "Derivatives",
            "Chain Rule",
            "Power Rule",
        }
        partial = await get(client, "/concepts", search="deriv")
        assert "Derivatives" in {c["name"] for c in partial.json()["data"]}
        none = await get(client, "/concepts", search="photosynthesis")
        assert none.json()["data"] == []

    async def test_search_treats_like_wildcards_literally(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept]
    ) -> None:
        assert (await get(client, "/concepts", search="%")).json()["data"] == []
        assert (await get(client, "/concepts", search="_")).json()["data"] == []

    async def test_mastery_filter_and_sort(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept], db: AsyncSession
    ) -> None:
        user_id = concepts["Limits"].user_id
        db.add_all(
            [
                StudentConceptMastery(
                    user_id=user_id, concept_id=concepts["Limits"].id, mastery_level=0.9
                ),
                StudentConceptMastery(
                    user_id=user_id, concept_id=concepts["Derivatives"].id, mastery_level=0.45
                ),
            ]
        )
        await db.commit()

        below = await get(client, "/concepts", mastery_below=0.5)
        assert {c["name"] for c in below.json()["data"]} == {
            "Derivatives",
            "Chain Rule",
            "Power Rule",
        }
        ranked = await get(client, "/concepts", sort="mastery_level", order="desc")
        top = ranked.json()["data"][0]
        assert top["name"] == "Limits"
        assert top["mastery"]["level"] == 0.9 and top["mastery"]["label"] == "mastered"

    async def test_filters_by_subject_and_document(
        self,
        client: httpx.AsyncClient,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        concepts: dict[str, Concept],
    ) -> None:
        subject = (
            await client.post(f"{API}/subjects", json={"name": "Biology"}, headers=A)
        ).json()["data"]
        bio = await uploader.upload(
            A, concept_text("Osmosis", "Water across membranes").encode(), subject_id=subject["id"]
        )
        await drain(processor, fake_queue)

        in_subject = await get(client, "/concepts", subject_id=subject["id"])
        assert [c["name"] for c in in_subject.json()["data"]] == ["Osmosis"]
        in_document = await get(client, "/concepts", document_id=bio["document_id"])
        assert [c["name"] for c in in_document.json()["data"]] == ["Osmosis"]
        chapter = await get(client, "/concepts", chapter_id=str(uuid.uuid4()))
        assert chapter.json()["data"] == []

    @pytest.mark.parametrize(
        "params",
        [
            {"mastery_below": 2},
            {"sort": "secret"},
            {"order": "sideways"},
            {"search": ""},
            {"per_page": 101},
        ],
    )
    async def test_invalid_query(self, client: httpx.AsyncClient, params: dict[str, Any]) -> None:
        resp = await get(client, "/concepts", **params)
        assert resp.status_code == 400

    async def test_pagination(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept]
    ) -> None:
        page2 = await get(client, "/concepts", page=2, per_page=3)
        assert [c["name"] for c in page2.json()["data"]] == ["Power Rule"]
        assert page2.json()["meta"]["pagination"] == {
            "total": 4,
            "page": 2,
            "per_page": 3,
            "total_pages": 2,
        }


class TestDetail:
    async def test_prerequisites_dependents_and_documents(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept]
    ) -> None:
        """AC-3.3: prerequisite relationships are detected, stored and exposed."""
        resp = await get(client, f"/concepts/{concepts['Derivatives'].id}")
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["name"] == "Derivatives"
        assert [p["name"] for p in data["prerequisites"]] == ["Limits"]
        assert data["prerequisites"][0]["mastery_level"] == 0.0
        assert {d["name"] for d in data["dependents"]} == {"Chain Rule", "Power Rule"}
        assert data["related_concepts"] == []
        assert data["misconceptions"] == []
        assert data["documents"][0]["title"] == "calculus"
        assert data["documents"][0]["section_count"] >= 1
        assert data["mastery"]["attempt_count"] == 0 and data["mastery"]["history"] == []
        assert data["metadata"]["origin"] == "extracted"

    async def test_related_relationships_are_viewed_from_the_concept(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept], db: AsyncSession
    ) -> None:
        d, c, p = concepts["Derivatives"], concepts["Chain Rule"], concepts["Power Rule"]
        db.add_all(
            [
                ConceptRelationship(
                    source_concept_id=c.id, target_concept_id=p.id, relationship_type="related"
                ),
                ConceptRelationship(
                    source_concept_id=d.id,
                    target_concept_id=c.id,
                    relationship_type="generalisation",
                ),
            ]
        )
        await db.commit()

        chain = (await get(client, f"/concepts/{c.id}")).json()["data"]
        assert {(r["name"], r["relationship"]) for r in chain["related_concepts"]} == {
            ("Power Rule", "related"),
            ("Derivatives", "generalisation"),
        }
        deriv = (await get(client, f"/concepts/{d.id}")).json()["data"]
        assert [(r["name"], r["relationship"]) for r in deriv["related_concepts"]] == [
            ("Chain Rule", "specialisation")
        ]
        power = (await get(client, f"/concepts/{p.id}")).json()["data"]
        assert [r["name"] for r in power["related_concepts"]] == ["Chain Rule"]

    async def test_mastery_and_misconceptions(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept], db: AsyncSession
    ) -> None:
        concept = concepts["Chain Rule"]
        user_id = concept.user_id
        misconception = Misconception(
            concept_id=concept.id,
            name="Forgets inner derivative",
            description="d/dx f(g(x)) = f'(g(x))",
        )
        db.add(misconception)
        await db.flush()
        db.add_all(
            [
                StudentMisconception(
                    user_id=user_id, misconception_id=misconception.id, status="active"
                ),
                StudentConceptMastery(
                    user_id=user_id,
                    concept_id=concept.id,
                    mastery_level=0.45,
                    confidence=0.7,
                    attempt_count=8,
                    correct_count=5,
                    streak=2,
                    history=[
                        {"date": "2026-09-15", "mastery": 0.3, "event": "practice"},
                        {"bad": "entry"},
                    ],
                ),
            ]
        )
        await db.commit()

        data = (await get(client, f"/concepts/{concept.id}")).json()["data"]
        assert data["mastery"]["level"] == 0.45 and data["mastery"]["label"] == "intermediate"
        assert data["mastery"]["attempt_count"] == 8 and data["mastery"]["streak"] == 2
        assert data["mastery"]["history"] == [
            {"date": "2026-09-15", "mastery": 0.3, "event": "practice"}
        ]
        assert data["misconceptions"] == [
            {"id": str(misconception.id), "name": "Forgets inner derivative", "status": "active"}
        ]

    async def test_unknown_concept(self, client: httpx.AsyncClient) -> None:
        resp = await get(client, f"/concepts/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


class TestGraph:
    async def test_neighbourhood_depths(
        self, client: httpx.AsyncClient, concepts: dict[str, Concept]
    ) -> None:
        limits = concepts["Limits"]
        one = (await get(client, f"/concepts/{limits.id}/graph")).json()["data"]
        assert {n["name"] for n in one["nodes"]} == {"Limits", "Derivatives"}
        assert [n["is_target"] for n in one["nodes"] if n["name"] == "Limits"] == [True]
        assert one["edges"] == [
            {
                "source": str(limits.id),
                "target": str(concepts["Derivatives"].id),
                "type": "prerequisite",
            }
        ]

        two = (await get(client, f"/concepts/{limits.id}/graph", depth=2)).json()["data"]
        assert {n["name"] for n in two["nodes"]} == set(concepts)
        assert len(two["edges"]) == 3
        assert all(n["mastery"] == 0.0 for n in two["nodes"])

    async def test_isolated_concept_and_validation(
        self, client: httpx.AsyncClient, db: AsyncSession, concepts: dict[str, Concept]
    ) -> None:
        user_id = await db.scalar(select(User.id).where(User.auth_id == "concept-owner"))
        lonely = Concept(user_id=user_id, name="Lonely")
        db.add(lonely)
        await db.commit()
        data = (await get(client, f"/concepts/{lonely.id}/graph")).json()["data"]
        assert data == {
            "nodes": [{"id": str(lonely.id), "name": "Lonely", "mastery": 0.0, "is_target": True}],
            "edges": [],
        }
        assert (await get(client, f"/concepts/{lonely.id}/graph", depth=4)).status_code == 400
        assert (await get(client, f"/concepts/{uuid.uuid4()}/graph")).status_code == 404
