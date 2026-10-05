"""Document processing pipeline against real PostgreSQL + Redis (mocked LLM, moto S3,
in-memory Qdrant): AC-3.1, AC-3.2, AC-3.3, AC-3.5 and the idempotency guarantees."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import AIBudgetExceededError, LLMServerError
from app.db.models import (
    AIInteraction,
    AITrace,
    Concept,
    ConceptRelationship,
    Document,
    DocumentSection,
    DocumentSectionConcept,
    User,
)
from app.integrations.qdrant import VectorStoreError
from app.services.content.document_processor import (
    DocumentProcessor,
    Outcome,
    section_id_for,
)
from tests.fakes import FakeLLMProvider, FakeOCR, FakeQueue, concept_text, make_pdf, make_png
from tests.integration.conftest import Uploader, drain
from tests.support import auth

A = auth("pipeline-user")

LESSON = "\n\n".join(
    [
        "# Chapter 5 Quadratics",
        concept_text(
            "Factoring Quadratics", "Writing ax^2+bx+c as a product of factors", filler=40
        ),
        "# The Formula",
        concept_text(
            "Quadratic Formula",
            "x = (-b ± sqrt(b^2-4ac)) / 2a",
            requires="Factoring Quadratics",
            filler=40,
        ),
        concept_text(
            "Discriminant",
            "b^2-4ac decides the number of roots",
            requires="Quadratic Formula",
            filler=40,
        ),
    ]
).encode()


async def count(db: AsyncSession, model: Any) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def doc_row(db: AsyncSession, document_id: str) -> Document:
    doc = await db.scalar(
        select(Document)
        .where(Document.id == uuid.UUID(document_id))
        .execution_options(populate_existing=True)
    )
    assert doc is not None
    return doc


async def reset_to_processing(db: AsyncSession, document_id: str) -> None:
    await db.execute(
        update(Document)
        .where(Document.id == uuid.UUID(document_id))
        .values(processing_status="processing")
    )
    await db.commit()


async def process_one(
    uploader: Uploader,
    processor: DocumentProcessor,
    queue: FakeQueue,
    data: bytes = LESSON,
    **kw: Any,
) -> dict[str, Any]:
    payload = await uploader.upload(A, data, **kw)
    payload["outcomes"] = await drain(processor, queue)
    return payload


class TestHappyPath:
    async def test_sections_concepts_relationships_and_links(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        payload = await process_one(uploader, processor, fake_queue)
        assert payload["outcomes"] == ["ready"]
        doc = await doc_row(db, payload["document_id"])
        assert doc.processing_status == "ready" and doc.processed_at is not None
        meta = doc.processing_metadata or {}
        assert meta["concept_count"] == 3 and meta["new_concept_count"] == 3
        assert meta["relationship_count"] == 2 and meta["dropped_cyclic_edges"] == 0
        assert meta["embedding_status"] == "complete" and meta["stage"] == "complete"

        sections = (
            await db.scalars(select(DocumentSection).order_by(DocumentSection.section_index))
        ).all()
        assert len(sections) == meta["chunk_count"] >= 2
        for s in sections:
            assert s.id == section_id_for(doc.id, s.section_index)  # deterministic ids
            assert s.token_count and s.token_count <= 1000
            assert s.embedding_id == str(s.id)
        assert sections[0].heading == "Chapter 5 Quadratics"

        concepts = {c.name: c for c in (await db.scalars(select(Concept))).all()}
        assert set(concepts) == {"Factoring Quadratics", "Quadratic Formula", "Discriminant"}
        qf = concepts["Quadratic Formula"]
        assert qf.description == "x = (-b ± sqrt(b^2-4ac)) / 2a"
        assert qf.difficulty_estimate == 0.5
        assert qf.metadata_ is not None and qf.metadata_["origin"] == "extracted"
        assert qf.metadata_["source_document_ids"] == [payload["document_id"]]

        edges = {
            (r.source_concept_id, r.target_concept_id, r.relationship_type)
            for r in (await db.scalars(select(ConceptRelationship))).all()
        }
        # AC-3.3: source is a prerequisite of target
        assert edges == {
            (concepts["Factoring Quadratics"].id, qf.id, "prerequisite"),
            (qf.id, concepts["Discriminant"].id, "prerequisite"),
        }
        links = (await db.scalars(select(DocumentSectionConcept))).all()
        assert {link.concept_id for link in links} == {c.id for c in concepts.values()}
        assert all(link.relevance_score == 1.0 for link in links)

    async def test_vectors_carry_user_filter_and_concepts(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        payload = await process_one(uploader, processor, fake_queue)
        user_id = await db.scalar(select(User.id).where(User.auth_id == "pipeline-user"))
        store = app.state.vector_store
        points, _ = await store.client.scroll(store.collection, with_payload=True, limit=100)
        assert len(points) == await count(db, DocumentSection)
        for point in points:
            assert point.payload["user_id"] == str(user_id)
            assert point.payload["document_id"] == payload["document_id"]
            assert point.payload["content_preview"]
        assert any(point.payload["concept_ids"] for point in points)

    async def test_progress_and_task_status_recorded(
        self, app: FastAPI, uploader: Uploader, processor: DocumentProcessor, fake_queue: FakeQueue
    ) -> None:
        payload = await process_one(uploader, processor, fake_queue)
        status = json.loads(await app.state.redis.get(f"task:{payload['task_id']}:status"))
        assert status == {
            "document_id": payload["document_id"],
            "status": "ready",
            "stage": "complete",
            "progress": 1.0,
        }

    async def test_pdf_with_scanned_page_uses_ocr(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        fake_ocr: FakeOCR,
        db: AsyncSession,
    ) -> None:
        pdf = make_pdf([concept_text("Vectors", "Magnitude and direction")], image_page=True)
        payload = await process_one(
            uploader, processor, fake_queue, pdf, filename="v.pdf", mime_type="application/pdf"
        )
        doc = await doc_row(db, payload["document_id"])
        assert doc.processing_status == "ready"
        assert (doc.processing_metadata or {})["ocr_pages"] == [2]
        assert set((await db.scalars(select(Concept.name))).all()) == {"Vectors", "Scanned Idea"}
        assert fake_ocr.calls == 1

    async def test_image_upload(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        payload = await process_one(
            uploader, processor, fake_queue, make_png(), filename="board.png", mime_type="image/png"
        )
        assert payload["outcomes"] == ["ready"]
        assert await db.scalar(select(Concept.name)) == "Scanned Idea"

    async def test_subject_scoping_of_concepts(
        self,
        client: httpx.AsyncClient,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        subject = (await client.post("/api/v1/subjects", json={"name": "Maths"}, headers=A)).json()[
            "data"
        ]
        await process_one(uploader, processor, fake_queue, subject_id=subject["id"])
        assert set((await db.scalars(select(Concept.subject_id))).all()) == {
            uuid.UUID(subject["id"])
        }
        # same concepts uploaded without a subject are distinct concepts (different scope)
        await process_one(uploader, processor, fake_queue)
        assert await count(db, Concept) == 6


class TestAuditing:
    async def test_every_llm_call_logged_with_cost(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        """AC-3.5: all LLM calls are logged in ai_interactions with cost estimates."""
        await process_one(uploader, processor, fake_queue)
        [trace] = (await db.scalars(select(AITrace))).all()
        interactions = (await db.scalars(select(AIInteraction))).all()

        assert trace.operation == "document_ingest" and trace.status == "completed"
        assert trace.completed_at is not None
        purposes = sorted(i.purpose for i in interactions)
        assert purposes == ["concept_extraction", "concept_relationships", "document_embedding"]
        for i in interactions:
            assert i.trace_id == trace.id and i.user_id == trace.user_id
            assert i.provider == "fake" and i.status == "success"
            assert i.cost_estimate is not None and i.cost_estimate > 0
            assert i.input_tokens > 0 and i.latency_ms is not None
            assert i.prompt_hash and len(i.prompt_hash) == 64
        extraction = next(i for i in interactions if i.purpose == "concept_extraction")
        meta = extraction.metadata_ or {}
        assert meta["prompt_name"] == "content/extract_concepts"
        assert meta["prompt_version"] == "1.0+1.0"
        assert meta["task"] == "concept_extraction"
        assert "Concept: Factoring Quadratics" in json.dumps(meta["request"])
        assert json.loads(meta["response"])["concepts"]
        assert extraction.output_tokens > 0 and extraction.response_hash
        assert trace.total_cost == pytest.approx(sum(i.cost_estimate or 0 for i in interactions))
        assert trace.total_tokens == sum(i.input_tokens + i.output_tokens for i in interactions)

    async def test_daily_cost_counter_in_redis(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        from app.ai.cost_tracker import cost_key

        await process_one(uploader, processor, fake_queue)
        user_id = await db.scalar(select(User.id).where(User.auth_id == "pipeline-user"))
        assert user_id is not None
        spent = float(await app.state.redis.get(cost_key(user_id)))
        total = await db.scalar(select(func.sum(AIInteraction.cost_estimate)))
        assert spent == pytest.approx(total)
        assert await app.state.ai_recorder.spent_today(user_id) == pytest.approx(total)

    async def test_budget_falls_back_to_database_without_redis(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        from app.ai.cost_tracker import AIUsageRecorder

        await process_one(uploader, processor, fake_queue)
        user_id = await db.scalar(select(User.id).where(User.auth_id == "pipeline-user"))
        assert user_id is not None
        no_redis = AIUsageRecorder(app.state.settings, app.state.sessionmaker, None)
        assert await no_redis.spent_today(user_id) == pytest.approx(
            await db.scalar(select(func.sum(AIInteraction.cost_estimate)))
        )

    async def test_failed_calls_are_logged_too(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        fake_llm.fail_with, fake_llm.fail_times = LLMServerError("upstream 502"), -1
        await process_one(uploader, processor, fake_queue)
        [interaction] = (await db.scalars(select(AIInteraction))).all()
        assert interaction.status == "error"
        assert interaction.error_message == "upstream 502"
        assert (await db.scalar(select(AITrace.status))) == "failed"


class TestIdempotency:
    async def test_reprocessing_does_not_duplicate(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        payload = await process_one(uploader, processor, fake_queue)
        before = (
            await count(db, DocumentSection),
            await count(db, Concept),
            await count(db, ConceptRelationship),
            await count(db, DocumentSectionConcept),
        )
        section_ids = set((await db.scalars(select(DocumentSection.id))).all())
        extraction_calls = fake_llm.calls.count("extraction")

        # Simulate a redelivered task (e.g. worker died after finishing).
        await reset_to_processing(db, payload["document_id"])
        outcome = await processor.process(
            uuid.UUID(payload["document_id"]), (await doc_row(db, payload["document_id"])).user_id
        )

        assert outcome is Outcome.READY
        after = (
            await count(db, DocumentSection),
            await count(db, Concept),
            await count(db, ConceptRelationship),
            await count(db, DocumentSectionConcept),
        )
        assert after == before
        assert set((await db.scalars(select(DocumentSection.id))).all()) == section_ids
        assert fake_llm.calls.count("extraction") == extraction_calls  # served from cache
        store = app.state.vector_store
        assert (await store.client.count(store.collection)).count == before[0]

    async def test_ready_documents_are_not_reprocessed(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        payload = await process_one(uploader, processor, fake_queue)
        doc = await doc_row(db, payload["document_id"])
        assert await processor.process(doc.id, doc.user_id) is Outcome.SKIPPED

    async def test_concurrent_worker_is_skipped(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        payload = await uploader.upload(A, LESSON)
        await app.state.redis.set(f"lock:document:{payload['document_id']}", "someone-else")
        doc = await doc_row(db, payload["document_id"])
        assert await processor.process(doc.id, doc.user_id) is Outcome.SKIPPED
        assert (await doc_row(db, payload["document_id"])).processing_status == "processing"

    async def test_other_users_document_is_never_processed(
        self, uploader: Uploader, processor: DocumentProcessor, db: AsyncSession
    ) -> None:
        payload = await uploader.upload(A, LESSON)
        assert (
            await processor.process(uuid.UUID(payload["document_id"]), uuid.uuid4())
            is Outcome.SKIPPED
        )


class TestConceptGraphAcrossDocuments:
    async def test_second_document_reuses_and_links_existing_concepts(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        await process_one(uploader, processor, fake_queue)
        fake_llm.duplicates = {"Quadratic Equation Formula": "Quadratic Formula"}
        second = "\n\n".join(
            [
                concept_text("factoring quadratics", "Same idea, different case"),
                concept_text("Quadratic Equation Formula", "Worded differently"),
                concept_text(
                    "Vieta Formulas", "Sum and product of roots", requires="Quadratic Formula"
                ),
            ]
        ).encode()
        payload = await process_one(uploader, processor, fake_queue, second, filename="second.txt")

        concepts = {c.name: c for c in (await db.scalars(select(Concept))).all()}
        assert set(concepts) == {
            "Factoring Quadratics",
            "Quadratic Formula",
            "Discriminant",
            "Vieta Formulas",
        }
        meta = (await doc_row(db, payload["document_id"])).processing_metadata or {}
        assert meta["new_concept_count"] == 1 and meta["merged_concept_count"] == 2
        sources = (concepts["Quadratic Formula"].metadata_ or {})["source_document_ids"]
        assert payload["document_id"] in sources and len(sources) == 2
        edge = await db.scalar(
            select(ConceptRelationship).where(
                ConceptRelationship.target_concept_id == concepts["Vieta Formulas"].id
            )
        )
        assert edge is not None and edge.source_concept_id == concepts["Quadratic Formula"].id

    async def test_prerequisite_cycles_are_rejected(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        await process_one(uploader, processor, fake_queue)  # Factoring → Formula → Discriminant
        cyclic = concept_text(
            "Factoring Quadratics", "Claims to need the discriminant", requires="Discriminant"
        )
        payload = await process_one(
            uploader, processor, fake_queue, cyclic.encode(), filename="cyclic.txt"
        )
        meta = (await doc_row(db, payload["document_id"])).processing_metadata or {}
        assert meta["dropped_cyclic_edges"] >= 1
        assert await count(db, ConceptRelationship) == 2


class TestFailures:
    async def test_transient_error_retries_then_fails_on_final_attempt(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        payload = await uploader.upload(A, LESSON)
        doc = await doc_row(db, payload["document_id"])
        fake_llm.fail_with, fake_llm.fail_times = LLMServerError("down"), -1

        assert await processor.process(doc.id, doc.user_id, final_attempt=False) is Outcome.RETRY
        retrying = await doc_row(db, payload["document_id"])
        assert retrying.processing_status == "processing"
        assert (retrying.processing_metadata or {})["stage"] == "retrying"

        assert await processor.process(doc.id, doc.user_id, final_attempt=True) is Outcome.FAILED
        failed = await doc_row(db, payload["document_id"])
        assert failed.processing_status == "failed"
        assert (failed.processing_metadata or {})["error"] == {
            "code": "LLM_UNAVAILABLE",
            "message": "The AI service is unavailable right now. Please retry later.",
        }

    async def test_transient_error_then_success(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        payload = await uploader.upload(A, LESSON)
        doc = await doc_row(db, payload["document_id"])
        fake_llm.fail_with, fake_llm.fail_times = LLMServerError("blip"), 1
        assert await processor.process(doc.id, doc.user_id, final_attempt=False) is Outcome.RETRY
        assert await processor.process(doc.id, doc.user_id, final_attempt=False) is Outcome.READY

    async def test_budget_exhausted_fails_without_retry(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        payload = await uploader.upload(A, LESSON)
        doc = await doc_row(db, payload["document_id"])
        fake_llm.fail_with, fake_llm.fail_times = AIBudgetExceededError("spent"), -1
        assert await processor.process(doc.id, doc.user_id, final_attempt=False) is Outcome.FAILED
        error = ((await doc_row(db, payload["document_id"])).processing_metadata or {})["error"]
        assert error["code"] == "AI_BUDGET_EXCEEDED"

    async def test_real_budget_check_stops_processing(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        from app.ai.cost_tracker import cost_key

        payload = await uploader.upload(A, LESSON)
        doc = await doc_row(db, payload["document_id"])
        await app.state.redis.set(cost_key(doc.user_id), "999")
        assert await processor.process(doc.id, doc.user_id) is Outcome.FAILED
        assert fake_llm.calls == []

    async def test_wrong_file_content_fails_permanently(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        fake_pdf = b"this is not a pdf"
        payload = await process_one(
            uploader, processor, fake_queue, fake_pdf, filename="x.pdf", mime_type="application/pdf"
        )
        doc = await doc_row(db, payload["document_id"])
        assert doc.processing_status == "failed"
        assert (doc.processing_metadata or {})["error"]["code"] == "FILE_TYPE_MISMATCH"
        assert await count(db, DocumentSection) == 0

    async def test_missing_object_is_transient(
        self, app: FastAPI, uploader: Uploader, processor: DocumentProcessor, db: AsyncSession
    ) -> None:
        payload = await uploader.upload(A, LESSON)
        await app.state.storage.delete(payload["s3_key"])
        doc = await doc_row(db, payload["document_id"])
        assert await processor.process(doc.id, doc.user_id, final_attempt=False) is Outcome.RETRY
        assert await processor.process(doc.id, doc.user_id) is Outcome.FAILED
        assert ((await doc_row(db, payload["document_id"])).processing_metadata or {})["error"][
            "code"
        ] == "STORAGE_UNAVAILABLE"

    async def test_unexpected_crash_marks_failed(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        db: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.content import document_processor

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("bug")

        monkeypatch.setattr(document_processor, "chunk_document", boom)
        payload = await uploader.upload(A, LESSON)
        doc = await doc_row(db, payload["document_id"])
        assert await processor.process(doc.id, doc.user_id) is Outcome.FAILED
        assert ((await doc_row(db, payload["document_id"])).processing_metadata or {})["error"][
            "code"
        ] == "INTERNAL_ERROR"

    async def test_document_deleted_mid_processing(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        db: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        payload = await uploader.upload(A, LESSON)
        doc = await doc_row(db, payload["document_id"])
        original = processor.graph.plan

        async def delete_then_plan(*args: Any, **kwargs: Any) -> Any:
            async with app.state.sessionmaker() as session:
                await session.execute(Document.__table__.delete().where(Document.id == doc.id))
                await session.commit()
            return await original(*args, **kwargs)

        monkeypatch.setattr(processor.graph, "plan", delete_then_plan)
        assert await processor.process(doc.id, doc.user_id) is Outcome.SKIPPED
        assert await count(db, DocumentSection) == 0
        assert await count(db, Concept) == 0


class TestIndexingDegradation:
    async def test_vector_store_outage_keeps_document_ready_and_schedules_reindex(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = app.state.vector_store

        async def down(points: Any) -> None:
            raise VectorStoreError("qdrant down")

        monkeypatch.setattr(store, "upsert_sections", down)
        payload = await process_one(uploader, processor, fake_queue)
        doc = await doc_row(db, payload["document_id"])
        assert doc.processing_status == "ready"
        assert (doc.processing_metadata or {})["embedding_status"] == "failed"
        assert fake_queue.reindexed == [(doc.id, doc.user_id)]
        assert set((await db.scalars(select(DocumentSection.embedding_id))).all()) == {None}

        monkeypatch.undo()
        assert await processor.reindex(doc.id, doc.user_id) is Outcome.READY
        doc = await doc_row(db, payload["document_id"])
        assert (doc.processing_metadata or {})["embedding_status"] == "complete"
        assert None not in set((await db.scalars(select(DocumentSection.embedding_id))).all())

    async def test_reindex_retry_and_final_failure(
        self,
        app: FastAPI,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        payload = await process_one(uploader, processor, fake_queue)
        doc = await doc_row(db, payload["document_id"])
        fake_llm.embed_fail_with = LLMServerError("embeddings down")
        assert await processor.reindex(doc.id, doc.user_id, final_attempt=False) is Outcome.RETRY
        assert await processor.reindex(doc.id, doc.user_id) is Outcome.FAILED
        assert ((await doc_row(db, payload["document_id"])).processing_metadata or {})[
            "embedding_status"
        ] == "failed"

    async def test_reindex_skips_unready_documents(
        self, uploader: Uploader, processor: DocumentProcessor, db: AsyncSession
    ) -> None:
        payload = await uploader.upload(A, LESSON)
        doc = await doc_row(db, payload["document_id"])
        assert await processor.reindex(doc.id, doc.user_id) is Outcome.SKIPPED

    async def test_without_vector_store_embedding_is_skipped(
        self,
        uploader: Uploader,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        db: AsyncSession,
    ) -> None:
        processor.vectors = None
        payload = await process_one(uploader, processor, fake_queue)
        doc = await doc_row(db, payload["document_id"])
        assert doc.processing_status == "ready"
        assert (doc.processing_metadata or {})["embedding_status"] == "skipped"
        assert await processor.reindex(doc.id, doc.user_id) is Outcome.SKIPPED
