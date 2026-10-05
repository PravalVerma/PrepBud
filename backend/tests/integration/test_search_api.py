"""Hybrid content search (AC-3.4): keyword (PostgreSQL FTS) + semantic (Qdrant)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIInteraction, AITrace, Concept
from app.integrations.qdrant import VectorStoreError
from app.services.content.document_processor import DocumentProcessor
from tests.fakes import FakeLLMProvider, FakeQueue, concept_text
from tests.integration.conftest import Uploader, drain
from tests.support import auth

API = "/api/v1"
A = auth("searcher")

ENGINES = "\n\n".join(
    [
        "# Engines",
        concept_text("Combustion Engine", "Burning fuel drives pistons", filler=4),
        "The car engine converts chemical energy into motion through controlled explosions.",
    ]
).encode()
PLANTS = "\n\n".join(
    [
        "# Plants",
        concept_text("Photosynthesis", "Light becomes chemical energy", filler=4),
        "Chlorophyll in leaves absorbs sunlight to make glucose from carbon dioxide and water.",
    ]
).encode()


@pytest.fixture
async def library(
    uploader: Uploader, processor: DocumentProcessor, fake_queue: FakeQueue
) -> dict[str, dict[str, Any]]:
    engines = await uploader.upload(A, ENGINES, filename="engines.txt")
    plants = await uploader.upload(A, PLANTS, filename="plants.txt")
    assert await drain(processor, fake_queue) == ["ready", "ready"]
    return {"engines": engines, "plants": plants}


async def search(
    client: httpx.AsyncClient, headers: dict[str, str] = A, **params: Any
) -> httpx.Response:
    return await client.get(f"{API}/search", params=params, headers=headers)


class TestModes:
    async def test_keyword_search(self, client: httpx.AsyncClient, library: dict[str, Any]) -> None:
        resp = await search(client, q="chlorophyll sunlight", mode="keyword")
        assert resp.status_code == 200
        assert resp.headers["X-Search-Mode"] == "keyword"
        [hit] = resp.json()["data"]
        assert hit["document_id"] == library["plants"]["document_id"]
        assert hit["document_title"] == "plants"
        assert hit["matched_by"] == ["keyword"]
        assert "**Chlorophyll**" in hit["snippet"]
        assert hit["heading"] == "Plants"
        assert hit["section_index"] == 0

    async def test_keyword_uses_stemming_and_websearch_syntax(
        self, client: httpx.AsyncClient, library: dict[str, Any]
    ) -> None:
        stemmed = await search(client, q="explosion", mode="keyword")
        assert [h["document_title"] for h in stemmed.json()["data"]] == ["engines"]
        phrase = await search(client, q='"chemical energy" -plants', mode="keyword")
        assert {h["document_title"] for h in phrase.json()["data"]} <= {"engines", "plants"}
        nothing = await search(client, q="zebra", mode="keyword")
        assert nothing.json()["data"] == []

    async def test_keyword_matches_any_term_ranked_by_coverage(
        self, client: httpx.AsyncClient, library: dict[str, Any]
    ) -> None:
        # "pistons" only occurs in engines, "glucose" only in plants: both sections match,
        # and the one covering more query terms ranks first.
        hits = (await search(client, q="glucose leaves pistons", mode="keyword")).json()["data"]
        assert [h["document_title"] for h in hits] == ["plants", "engines"]

    async def test_keyword_exclusion_keeps_strict_matching(
        self, client: httpx.AsyncClient, library: dict[str, Any]
    ) -> None:
        hits = (await search(client, q="energy -glucose", mode="keyword")).json()["data"]
        assert [h["document_title"] for h in hits] == ["engines"]

    async def test_semantic_finds_meaning_without_shared_words(
        self, client: httpx.AsyncClient, fake_llm: FakeLLMProvider, library: dict[str, Any]
    ) -> None:
        # The fake embedder treats "automobile" as a synonym of "car".
        fake_llm.synonyms = {"automobile": "car", "motor": "engine"}
        keyword = await search(client, q="automobile motor", mode="keyword")
        assert keyword.json()["data"] == []

        semantic = await search(client, q="automobile motor", mode="semantic")
        hits = semantic.json()["data"]
        assert hits[0]["document_title"] == "engines"
        assert hits[0]["matched_by"] == ["semantic"]
        assert hits[0]["snippet"]

    async def test_hybrid_merges_and_ranks(
        self, client: httpx.AsyncClient, fake_llm: FakeLLMProvider, library: dict[str, Any]
    ) -> None:
        resp = await search(client, q="chemical energy engine")
        assert resp.headers["X-Search-Mode"] == "hybrid"
        hits = resp.json()["data"]
        assert hits[0]["document_title"] == "engines"
        assert hits[0]["matched_by"] == ["keyword", "semantic"]
        scores = [h["score"] for h in hits]
        assert scores == sorted(scores, reverse=True)
        assert resp.json()["meta"]["pagination"]["total"] == len(hits) == 2

    async def test_semantic_query_is_audited(
        self, client: httpx.AsyncClient, db: AsyncSession, library: dict[str, Any]
    ) -> None:
        before = await db.scalar(select(func.count()).select_from(AIInteraction))
        await search(client, q="energy", mode="semantic")
        trace = await db.scalar(select(AITrace).where(AITrace.operation == "content_search"))
        assert trace is not None and trace.status == "completed"
        assert await db.scalar(select(func.count()).select_from(AIInteraction)) == (before or 0) + 1


class TestFiltersAndPaging:
    async def test_document_concept_and_subject_filters(
        self, client: httpx.AsyncClient, db: AsyncSession, library: dict[str, Any]
    ) -> None:
        only_plants = await search(client, q="energy", document_id=library["plants"]["document_id"])
        assert {h["document_title"] for h in only_plants.json()["data"]} == {"plants"}

        engine_concept = await db.scalar(
            select(Concept.id).where(Concept.name == "Combustion Engine")
        )
        by_concept = await search(client, q="energy", concept_id=str(engine_concept))
        assert {h["document_title"] for h in by_concept.json()["data"]} == {"engines"}

        no_subject = await search(client, q="energy", subject_id=str(uuid.uuid4()))
        assert no_subject.json()["data"] == []

    async def test_pagination(self, client: httpx.AsyncClient, library: dict[str, Any]) -> None:
        first = await search(client, q="energy", per_page=1)
        second = await search(client, q="energy", per_page=1, page=2)
        assert first.json()["meta"]["pagination"]["total"] == 2
        assert first.json()["data"][0]["section_id"] != second.json()["data"][0]["section_id"]

    @pytest.mark.parametrize(
        "params", [{}, {"q": ""}, {"q": "x" * 501}, {"q": "a", "mode": "fuzzy"}]
    )
    async def test_validation(self, client: httpx.AsyncClient, params: dict[str, Any]) -> None:
        assert (await search(client, **params)).status_code == 400

    async def test_whitespace_query_returns_nothing(self, client: httpx.AsyncClient) -> None:
        resp = await search(client, q="   ")
        assert resp.status_code == 200 and resp.json()["data"] == []


class TestDegradation:
    async def test_hybrid_falls_back_to_keyword_when_qdrant_is_down(
        self,
        app: FastAPI,
        client: httpx.AsyncClient,
        library: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def down(*args: Any, **kwargs: Any) -> Any:
            raise VectorStoreError("down")

        monkeypatch.setattr(app.state.vector_store, "search", down)
        resp = await search(client, q="chlorophyll")
        assert resp.status_code == 200
        assert resp.headers["X-Search-Degraded"] == "true"
        assert resp.headers["X-Search-Mode"] == "keyword"
        assert [h["matched_by"] for h in resp.json()["data"]] == [["keyword"]]

        semantic = await search(client, q="chlorophyll", mode="semantic")
        assert semantic.status_code == 503

    async def test_without_vector_store(
        self, app: FastAPI, client: httpx.AsyncClient, library: dict[str, Any]
    ) -> None:
        app.state.vector_store = None
        resp = await search(client, q="chlorophyll")
        assert resp.headers["X-Search-Degraded"] == "true"
        assert len(resp.json()["data"]) == 1

    async def test_budget_exhausted_degrades(
        self, app: FastAPI, client: httpx.AsyncClient, db: AsyncSession, library: dict[str, Any]
    ) -> None:
        from app.ai.cost_tracker import cost_key

        user_id = await db.scalar(select(AITrace.user_id))
        await app.state.redis.set(cost_key(user_id), "1000")
        resp = await search(client, q="chlorophyll")
        assert resp.headers.get("X-Search-Degraded") == "true"
        assert (await search(client, q="chlorophyll", mode="semantic")).status_code == 503

    async def test_stale_vectors_for_deleted_sections_are_ignored(
        self, app: FastAPI, client: httpx.AsyncClient, db: AsyncSession, library: dict[str, Any]
    ) -> None:
        from app.db.models import Document

        # Remove the row but leave the vectors (as if Qdrant cleanup lagged behind).
        await db.execute(
            Document.__table__.delete().where(
                Document.id == uuid.UUID(library["plants"]["document_id"])
            )
        )
        await db.commit()
        resp = await search(client, q="chlorophyll sunlight leaves", mode="semantic")
        assert all(h["document_title"] != "plants" for h in resp.json()["data"])
