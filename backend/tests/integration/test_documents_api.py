"""Document upload flow and CRUD (API_CONTRACT §3.5, SECURITY_MODEL §5.2, AC-3.1)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import LLMResponseError
from app.db.models import Concept, Document, DocumentSection
from app.services.content.document_processor import DocumentProcessor
from tests.fakes import FakeLLMProvider, FakeQueue, concept_text, make_pdf
from tests.integration.conftest import Uploader, drain
from tests.support import TEST_BUCKET, auth

API = "/api/v1"
A = auth("doc-owner")
TEXT = concept_text("Photosynthesis", "Plants turn light into chemical energy").encode()


def s3_keys(app: FastAPI) -> list[str]:
    resp = app.state.storage._client.list_objects_v2(Bucket=TEST_BUCKET)
    return [o["Key"] for o in resp.get("Contents", [])]


class TestUploadUrl:
    async def test_creates_pending_document_and_presigned_url(
        self, uploader: Uploader, db: AsyncSession
    ) -> None:
        resp = await uploader.request_url(
            A, filename="../Chapter 5 – Quadratics.pdf", mime_type="application/pdf", size=2048
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        doc_id = uuid.UUID(data["document_id"])
        assert data["s3_key"].startswith("uploads/") and data["s3_key"].endswith(
            f"/{doc_id}/Chapter 5 _ Quadratics.pdf"
        )
        assert data["expires_in_seconds"] == 3600
        assert data["upload_headers"] == {"Content-Type": "application/pdf"}
        assert "X-Amz-Signature=" in data["upload_url"]
        doc = await db.scalar(select(Document).where(Document.id == doc_id))
        assert doc is not None
        assert doc.processing_status == "pending"
        assert doc.title == "Chapter 5 – Quadratics"
        assert doc.file_size_bytes == 2048

    async def test_custom_title_and_curriculum(
        self, uploader: Uploader, client: httpx.AsyncClient
    ) -> None:
        subject = (await client.post(f"{API}/subjects", json={"name": "Maths"}, headers=A)).json()[
            "data"
        ]
        course = (
            await client.post(
                f"{API}/subjects/{subject['id']}/courses", json={"name": "Algebra"}, headers=A
            )
        ).json()["data"]

        resp = await uploader.request_url(A, title="My notes", course_id=course["id"])

        doc = (
            await client.get(f"{API}/documents/{resp.json()['data']['document_id']}", headers=A)
        ).json()["data"]
        assert doc["title"] == "My notes"
        assert doc["course_id"] == course["id"]
        assert doc["subject_id"] == subject["id"]  # inferred from the course

    async def test_course_must_belong_to_subject(
        self, uploader: Uploader, client: httpx.AsyncClient
    ) -> None:
        s1 = (await client.post(f"{API}/subjects", json={"name": "S1"}, headers=A)).json()["data"]
        s2 = (await client.post(f"{API}/subjects", json={"name": "S2"}, headers=A)).json()["data"]
        course = (
            await client.post(f"{API}/subjects/{s1['id']}/courses", json={"name": "C"}, headers=A)
        ).json()["data"]
        resp = await uploader.request_url(A, subject_id=s2["id"], course_id=course["id"])
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "UNPROCESSABLE_ENTITY"

    async def test_other_users_subject_is_404(
        self, uploader: Uploader, client: httpx.AsyncClient
    ) -> None:
        theirs = (
            await client.post(f"{API}/subjects", json={"name": "Theirs"}, headers=auth("other"))
        ).json()["data"]
        assert (await uploader.request_url(A, subject_id=theirs["id"])).status_code == 404
        assert (await uploader.request_url(A, course_id=str(uuid.uuid4()))).status_code == 404

    @pytest.mark.parametrize(
        ("filename", "mime_type", "size"),
        [
            ("virus.exe", "application/x-msdownload", 10),
            ("notes.pdf", "text/plain", 10),
            ("notes.txt", "text/plain", 0),
            ("big.pdf", "application/pdf", 50 * 1024 * 1024 + 1),
        ],
    )
    async def test_rejects_invalid_uploads(
        self, uploader: Uploader, filename: str, mime_type: str, size: int
    ) -> None:
        resp = await uploader.request_url(A, filename=filename, mime_type=mime_type, size=size)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_too_large_reports_field(self, uploader: Uploader) -> None:
        resp = await uploader.request_url(
            A, filename="big.pdf", mime_type="application/pdf", size=10**9
        )
        details = resp.json()["error"]["details"]
        assert details["errors"][0]["loc"] == ["body", "file_size_bytes"]
        assert details["max_bytes"] == 50 * 1024 * 1024

    async def test_storage_not_configured_is_503(self, app: FastAPI, uploader: Uploader) -> None:
        app.state.storage = None
        resp = await uploader.request_url(A)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


class TestConfirmUpload:
    async def test_confirm_queues_processing(
        self, uploader: Uploader, client: httpx.AsyncClient, fake_queue: FakeQueue
    ) -> None:
        payload = await uploader.upload(A, TEXT, confirm=False)
        doc_id = payload["document_id"]

        resp = await client.post(f"{API}/documents/{doc_id}/confirm-upload", headers=A)

        assert resp.status_code == 202
        data = resp.json()["data"]
        assert data["processing_status"] == "processing"
        assert data["document_id"] == doc_id
        [(queued_doc, _, task_id)] = fake_queue.documents
        assert str(queued_doc) == doc_id and task_id == data["task_id"]
        listed = (await client.get(f"{API}/documents/{doc_id}", headers=A)).json()["data"]
        assert listed["processing_status"] == "processing"
        assert listed["processing_metadata"] == {"stage": "queued", "progress": 0.0}

    async def test_confirm_is_idempotent_while_processing(
        self, uploader: Uploader, client: httpx.AsyncClient, fake_queue: FakeQueue
    ) -> None:
        payload = await uploader.upload(A, TEXT)
        again = await client.post(
            f"{API}/documents/{payload['document_id']}/confirm-upload", headers=A
        )
        assert again.status_code == 202
        assert again.json()["data"]["task_id"] == payload["task_id"]
        assert len(fake_queue.documents) == 1

    async def test_confirm_before_upload_is_422(
        self, uploader: Uploader, client: httpx.AsyncClient
    ) -> None:
        data = (await uploader.request_url(A)).json()["data"]
        resp = await client.post(f"{API}/documents/{data['document_id']}/confirm-upload", headers=A)
        assert resp.status_code == 422
        assert resp.json()["error"]["details"]["reason"] == "UPLOAD_NOT_FOUND"

    async def test_size_mismatch_fails_document_and_removes_object(
        self, app: FastAPI, uploader: Uploader, client: httpx.AsyncClient
    ) -> None:
        data = (await uploader.request_url(A, size=10)).json()["data"]
        uploader.put(data["s3_key"], b"x" * 999, "text/plain")

        resp = await client.post(f"{API}/documents/{data['document_id']}/confirm-upload", headers=A)

        assert resp.status_code == 422
        assert resp.json()["error"]["details"]["reason"] == "FILE_SIZE_MISMATCH"
        doc = (await client.get(f"{API}/documents/{data['document_id']}", headers=A)).json()["data"]
        assert doc["processing_status"] == "failed"
        assert doc["processing_metadata"]["error"]["code"] == "FILE_SIZE_MISMATCH"
        assert data["s3_key"] not in s3_keys(app)

    async def test_content_type_mismatch(
        self, uploader: Uploader, client: httpx.AsyncClient
    ) -> None:
        data = (await uploader.request_url(A, size=4)).json()["data"]
        uploader.put(data["s3_key"], b"abcd", "image/png")
        resp = await client.post(f"{API}/documents/{data['document_id']}/confirm-upload", headers=A)
        assert resp.json()["error"]["details"]["reason"] == "FILE_TYPE_MISMATCH"

    async def test_ready_document_cannot_be_reconfirmed(
        self,
        uploader: Uploader,
        client: httpx.AsyncClient,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
    ) -> None:
        payload = await uploader.upload(A, TEXT)
        await drain(processor, fake_queue)
        resp = await client.post(
            f"{API}/documents/{payload['document_id']}/confirm-upload", headers=A
        )
        assert resp.status_code == 409

    async def test_failed_document_can_be_retried(
        self,
        uploader: Uploader,
        client: httpx.AsyncClient,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
        fake_llm: FakeLLMProvider,
    ) -> None:
        fake_llm.fail_with, fake_llm.fail_times = LLMResponseError("rejected"), -1
        payload = await uploader.upload(A, TEXT)
        assert await drain(processor, fake_queue) == ["failed"]
        doc_url = f"{API}/documents/{payload['document_id']}"
        failed = (await client.get(doc_url, headers=A)).json()["data"]
        assert failed["processing_metadata"]["error"]["code"] == "LLM_BAD_RESPONSE"

        fake_llm.fail_with = None
        resp = await client.post(f"{doc_url}/confirm-upload", headers=A)

        assert resp.status_code == 202
        retrying = (await client.get(doc_url, headers=A)).json()["data"]
        assert retrying["processing_status"] == "processing"
        assert "error" not in retrying["processing_metadata"]
        assert await drain(processor, fake_queue) == ["ready"]

    async def test_queue_outage_rolls_back_to_pending(
        self, uploader: Uploader, client: httpx.AsyncClient, fake_queue: FakeQueue
    ) -> None:
        payload = await uploader.upload(A, TEXT, confirm=False)
        fake_queue.fail = True
        resp = await client.post(
            f"{API}/documents/{payload['document_id']}/confirm-upload", headers=A
        )
        assert resp.status_code == 503
        doc = (await client.get(f"{API}/documents/{payload['document_id']}", headers=A)).json()[
            "data"
        ]
        assert doc["processing_status"] == "pending"

    async def test_unknown_document(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(f"{API}/documents/{uuid.uuid4()}/confirm-upload", headers=A)
        assert resp.status_code == 404


class TestListGetDelete:
    async def test_list_filters_and_paginates(
        self,
        uploader: Uploader,
        client: httpx.AsyncClient,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
    ) -> None:
        subject = (await client.post(f"{API}/subjects", json={"name": "Bio"}, headers=A)).json()[
            "data"
        ]
        await uploader.upload(A, TEXT, filename="one.txt", subject_id=subject["id"])
        await drain(processor, fake_queue)
        await uploader.upload(A, TEXT, filename="two.txt", confirm=False)
        await uploader.upload(A, TEXT, filename="three.txt", confirm=False)

        everything = (await client.get(f"{API}/documents?per_page=2", headers=A)).json()
        ready = (await client.get(f"{API}/documents?status=ready", headers=A)).json()["data"]
        by_subject = (
            await client.get(f"{API}/documents?subject_id={subject['id']}", headers=A)
        ).json()["data"]

        assert everything["meta"]["pagination"] == {
            "total": 3,
            "page": 1,
            "per_page": 2,
            "total_pages": 2,
        }
        assert [d["source_filename"] for d in everything["data"]] == [
            "three.txt",
            "two.txt",
        ]  # newest first
        assert [d["source_filename"] for d in ready] == ["one.txt"]
        assert [d["source_filename"] for d in by_subject] == ["one.txt"]
        meta = ready[0]["processing_metadata"]
        assert meta["chunk_count"] == 1 and meta["concept_count"] == 1
        assert "task_id" not in meta and "attempts" not in meta

    async def test_invalid_status_filter(self, client: httpx.AsyncClient) -> None:
        assert (await client.get(f"{API}/documents?status=done", headers=A)).status_code == 400

    async def test_detail_includes_concepts(
        self,
        uploader: Uploader,
        client: httpx.AsyncClient,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
    ) -> None:
        payload = await uploader.upload(A, TEXT)
        await drain(processor, fake_queue)
        doc = (await client.get(f"{API}/documents/{payload['document_id']}", headers=A)).json()[
            "data"
        ]
        assert doc["processing_status"] == "ready"
        assert doc["section_count"] == 1
        assert [c["name"] for c in doc["concepts"]] == ["Photosynthesis"]
        assert doc["concepts"][0]["section_count"] == 1
        assert doc["processed_at"]

    async def test_delete_removes_file_vectors_rows_and_orphan_concepts(
        self,
        app: FastAPI,
        uploader: Uploader,
        client: httpx.AsyncClient,
        db: AsyncSession,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
    ) -> None:
        keep = await uploader.upload(A, concept_text("Osmosis", "Water through membranes").encode())
        gone = await uploader.upload(A, TEXT)
        await drain(processor, fake_queue)
        vectors = app.state.vector_store
        user_id = await db.scalar(
            select(Document.user_id).where(Document.id == uuid.UUID(gone["document_id"]))
        )
        assert await vectors.count_document_points(user_id, uuid.UUID(gone["document_id"])) == 1

        resp = await client.delete(f"{API}/documents/{gone['document_id']}", headers=A)

        assert resp.status_code == 204
        assert (
            await client.get(f"{API}/documents/{gone['document_id']}", headers=A)
        ).status_code == 404
        assert gone["s3_key"] not in s3_keys(app) and keep["s3_key"] in s3_keys(app)
        assert await vectors.count_document_points(user_id, uuid.UUID(gone["document_id"])) == 0
        assert await vectors.count_document_points(user_id, uuid.UUID(keep["document_id"])) == 1
        names = set((await db.scalars(select(Concept.name))).all())
        assert names == {"Osmosis"}  # orphaned extracted concept removed
        assert await db.scalar(select(func.count()).select_from(DocumentSection)) == 1

    async def test_delete_keeps_concepts_with_learning_data(
        self,
        uploader: Uploader,
        client: httpx.AsyncClient,
        db: AsyncSession,
        processor: DocumentProcessor,
        fake_queue: FakeQueue,
    ) -> None:
        from app.db.models import StudentConceptMastery

        payload = await uploader.upload(A, TEXT)
        await drain(processor, fake_queue)
        concept = await db.scalar(select(Concept))
        assert concept is not None
        db.add(
            StudentConceptMastery(user_id=concept.user_id, concept_id=concept.id, mastery_level=0.4)
        )
        await db.commit()

        await client.delete(f"{API}/documents/{payload['document_id']}", headers=A)

        assert await db.scalar(select(func.count()).select_from(Concept)) == 1

    async def test_delete_fails_closed_when_storage_unreachable(
        self, app: FastAPI, uploader: Uploader, client: httpx.AsyncClient
    ) -> None:
        from app.integrations.s3 import StorageError

        payload = await uploader.upload(A, TEXT, confirm=False)

        async def broken(key: str) -> None:
            raise StorageError("down")

        app.state.storage.delete = broken
        resp = await client.delete(f"{API}/documents/{payload['document_id']}", headers=A)
        assert resp.status_code == 503
        assert (
            await client.get(f"{API}/documents/{payload['document_id']}", headers=A)
        ).status_code == 200

    async def test_delete_unknown_is_404(self, client: httpx.AsyncClient) -> None:
        assert (
            await client.delete(f"{API}/documents/{uuid.uuid4()}", headers=A)
        ).status_code == 404


class TestUploadRateLimit:
    @pytest.fixture
    def settings_overrides(self) -> dict[str, Any]:
        return {"rate_limit_uploads_per_hour": 2}

    async def test_uploads_limited_per_hour(self, uploader: Uploader) -> None:
        statuses = [(await uploader.request_url(A)).status_code for _ in range(3)]
        assert statuses == [200, 200, 429]
        other = await uploader.request_url(auth("someone-else"))
        assert other.status_code == 200


async def test_pdf_upload_end_to_end(
    uploader: Uploader,
    client: httpx.AsyncClient,
    processor: DocumentProcessor,
    fake_queue: FakeQueue,
) -> None:
    """AC-3.1: pending → processing → ready for a real PDF."""
    pdf = make_pdf(
        [
            concept_text("Factoring Quadratics", "Writing a quadratic as a product"),
            concept_text(
                "Quadratic Formula", "Solving any quadratic", requires="Factoring Quadratics"
            ),
        ]
    )
    data = (
        await uploader.request_url(
            A, filename="ch5.pdf", mime_type="application/pdf", size=len(pdf)
        )
    ).json()["data"]
    doc_url = f"{API}/documents/{data['document_id']}"
    assert (await client.get(doc_url, headers=A)).json()["data"]["processing_status"] == "pending"

    uploader.put(data["s3_key"], pdf, "application/pdf")
    await client.post(f"{doc_url}/confirm-upload", headers=A)
    assert (await client.get(doc_url, headers=A)).json()["data"][
        "processing_status"
    ] == "processing"

    assert await drain(processor, fake_queue) == ["ready"]
    doc = (await client.get(doc_url, headers=A)).json()["data"]
    assert doc["processing_status"] == "ready"
    assert doc["processing_metadata"]["page_count"] == 2
    assert doc["processing_metadata"]["stage"] == "complete"
    assert doc["processing_metadata"]["embedding_status"] == "complete"
    assert {c["name"] for c in doc["concepts"]} == {"Factoring Quadratics", "Quadratic Formula"}
