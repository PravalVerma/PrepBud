"""Document endpoints (API_CONTRACT §3.5).

Upload flow: ``POST /documents/upload-url`` creates a ``pending`` document and returns
a presigned S3 PUT URL → the browser uploads directly to S3 → ``POST
/documents/{id}/confirm-upload`` verifies the object and queues processing (202).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    OptionalStorage,
    PageParams,
    Queue,
    Storage,
    VectorStore,
    rate_limit_uploads,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.models import Document, DocumentSection, DocumentSectionConcept
from app.db.repositories.concept import ConceptRepository
from app.db.repositories.curriculum import CourseRepository, SubjectRepository
from app.db.repositories.document import DocumentRepository
from app.domain.common import Envelope, PaginatedEnvelope, envelope, paginated
from app.domain.content import (
    ConfirmUploadResponse,
    DocumentConceptRef,
    DocumentDetail,
    DocumentRead,
    ProcessingStatus,
    UploadUrlRequest,
    UploadUrlResponse,
    can_transition,
    sanitize_filename,
    title_from_filename,
)
from app.integrations.qdrant import VectorStoreError
from app.integrations.s3 import StorageError

logger = get_logger(__name__)
router = APIRouter(tags=["documents"])


async def _resolve_curriculum(
    session: AsyncSession,
    user_id: uuid.UUID,
    subject_id: uuid.UUID | None,
    course_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    if course_id is not None:
        course = await CourseRepository(session).get_by_id(course_id, user_id)
        if course is None:
            raise NotFoundError("Course not found")
        if subject_id is not None and course.subject_id != subject_id:
            raise UnprocessableEntityError(
                "The course does not belong to the given subject", details={"field": "course_id"}
            )
        return course.subject_id, course_id
    if (
        subject_id is not None
        and await SubjectRepository(session).get_by_id(subject_id, user_id) is None
    ):
        raise NotFoundError("Subject not found")
    return subject_id, None


async def _get_or_404(
    session: AsyncSession, document_id: uuid.UUID, user_id: uuid.UUID
) -> Document:
    doc = await DocumentRepository(session).get_by_id(document_id, user_id)
    if doc is None:
        raise NotFoundError("Document not found")
    return doc


@router.post(
    "/documents/upload-url",
    response_model=Envelope[UploadUrlResponse],
    dependencies=[Depends(rate_limit_uploads)],
    summary="Get a presigned upload URL",
)
async def create_upload_url(
    body: UploadUrlRequest,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    storage: Storage,
    settings: AppSettings,
) -> Envelope[UploadUrlResponse]:
    limit = settings.content.upload_max_bytes
    if body.file_size_bytes > limit:
        raise ValidationError(
            "File is too large",
            details={
                "errors": [
                    {
                        "loc": ["body", "file_size_bytes"],
                        "message": f"File must be at most {limit // (1024 * 1024)} MB",
                        "type": "too_large",
                    }
                ],
                "max_bytes": limit,
            },
        )
    subject_id, course_id = await _resolve_curriculum(
        session, user.id, body.subject_id, body.course_id
    )

    filename = sanitize_filename(body.filename)
    document_id = uuid.uuid4()
    key = f"uploads/{user.id}/{document_id}/{filename}"
    expires = settings.s3_presign_expiry_seconds
    try:
        url = storage.presign_put(key, body.mime_type, expires)
    except Exception as exc:
        raise ServiceUnavailableError("File storage is unavailable") from exc

    session.add(
        Document(
            id=document_id,
            user_id=user.id,
            title=body.title or title_from_filename(body.filename),
            source_filename=filename,
            mime_type=body.mime_type,
            file_size_bytes=body.file_size_bytes,
            s3_key=key,
            processing_status="pending",
            processing_metadata={},
            subject_id=subject_id,
            course_id=course_id,
        )
    )
    await session.commit()
    return envelope(
        request,
        UploadUrlResponse(
            upload_url=url,
            document_id=document_id,
            s3_key=key,
            expires_in_seconds=expires,
            upload_headers={"Content-Type": body.mime_type},
        ),
    )


@router.post(
    "/documents/{document_id}/confirm-upload",
    response_model=Envelope[ConfirmUploadResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Confirm the upload and start processing",
)
async def confirm_upload(
    document_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    storage: Storage,
    queue: Queue,
    settings: AppSettings,
) -> Envelope[ConfirmUploadResponse]:
    repo = DocumentRepository(session)
    doc = await _get_or_404(session, document_id, user.id)
    current = doc.processing_status or "pending"
    if current == "processing":  # idempotent: already queued
        return envelope(
            request,
            ConfirmUploadResponse(
                document_id=doc.id,
                processing_status="processing",
                task_id=(doc.processing_metadata or {}).get("task_id"),
            ),
        )
    if not can_transition(current, "processing"):
        raise ConflictError("This document has already been processed")

    try:
        info = await storage.head(doc.s3_key)
    except StorageError as exc:
        raise ServiceUnavailableError("File storage is unavailable") from exc
    if info is None:
        raise UnprocessableEntityError(
            "The file has not been uploaded yet", details={"reason": "UPLOAD_NOT_FOUND"}
        )
    size_ok = info.size <= settings.content.upload_max_bytes and (
        not doc.file_size_bytes or info.size == doc.file_size_bytes
    )
    type_ok = not info.content_type or info.content_type.split(";")[0].strip() == doc.mime_type
    if not (size_ok and type_ok):
        reason = "FILE_SIZE_MISMATCH" if not size_ok else "FILE_TYPE_MISMATCH"
        doc.processing_status = "failed"
        doc.processing_metadata = {
            "stage": "failed",
            "error": {
                "code": reason,
                "message": "The uploaded file does not match what was declared",
            },
        }
        await session.commit()
        try:
            await storage.delete(doc.s3_key)
        except StorageError:
            logger.warning("could not delete rejected upload", extra={"document_id": str(doc.id)})
        raise UnprocessableEntityError(
            "The uploaded file does not match what was declared", details={"reason": reason}
        )

    locked = await repo.get_for_update(document_id, user.id)
    if locked is None or locked.processing_status != current:
        raise ConflictError("The document changed; reload and try again")
    task_id = str(uuid.uuid4())
    attempts = int((locked.processing_metadata or {}).get("attempts", 0)) + 1
    locked.processing_status = "processing"
    locked.processing_metadata = {
        "stage": "queued",
        "progress": 0.0,
        "task_id": task_id,
        "attempts": attempts,
    }
    await session.commit()
    try:
        await queue.enqueue_document(doc.id, user.id, task_id=task_id)
    except Exception as exc:
        logger.exception(
            "could not enqueue document processing", extra={"document_id": str(doc.id)}
        )
        locked.processing_status = current
        locked.processing_metadata = {}
        await session.commit()
        raise ServiceUnavailableError("Processing queue is unavailable; try again shortly") from exc
    return envelope(
        request,
        ConfirmUploadResponse(document_id=doc.id, processing_status="processing", task_id=task_id),
    )


@router.get("/documents", response_model=PaginatedEnvelope[DocumentRead], summary="List documents")
async def list_documents(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    page: PageParams,
    status_: ProcessingStatus | None = Query(None, alias="status"),
    subject_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
) -> PaginatedEnvelope[DocumentRead]:
    result = await DocumentRepository(session).list_documents(
        user.id, page, status=status_, subject_id=subject_id, course_id=course_id
    )
    return paginated(
        request,
        [DocumentRead.model_validate(d) for d in result.items],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
    )


@router.get("/documents/{document_id}", response_model=Envelope[DocumentDetail])
async def get_document(
    document_id: uuid.UUID, request: Request, user: CurrentUser, session: DbSession
) -> Envelope[DocumentDetail]:
    repo = DocumentRepository(session)
    doc = await _get_or_404(session, document_id, user.id)
    concepts = await repo.linked_concepts(doc.id, user.id)
    detail = DocumentDetail.model_validate(doc).model_copy(
        update={
            "section_count": await repo.section_count(doc.id),
            "concepts": [
                DocumentConceptRef(id=c.id, name=c.name, section_count=c.section_count)
                for c in concepts
            ],
        }
    )
    return envelope(request, detail)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    storage: OptionalStorage,
    vectors: VectorStore,
) -> Response:
    """Remove the file (S3), its vectors (Qdrant) and its rows (PostgreSQL) — SECURITY_MODEL §4.3.

    External copies are removed first: if either store is unreachable nothing is
    deleted and the client gets 503, so no orphaned copy of the student's file remains.
    """
    doc = await _get_or_404(session, document_id, user.id)
    try:
        if storage is not None:
            await storage.delete(doc.s3_key)
        if vectors is not None:
            await vectors.delete_document(user.id, doc.id)
    except (StorageError, VectorStoreError) as exc:
        raise ServiceUnavailableError("Could not delete the document right now; try again") from exc

    linked = (
        await session.scalars(
            select(func.distinct(DocumentSectionConcept.concept_id))
            .join(DocumentSection, DocumentSection.id == DocumentSectionConcept.document_section_id)
            .where(DocumentSection.document_id == doc.id)
        )
    ).all()
    await session.delete(doc)
    await session.flush()
    await ConceptRepository(session).delete_orphaned_extracted(user.id, list(linked))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
