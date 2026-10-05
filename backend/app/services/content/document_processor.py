"""Document ingestion pipeline (ARCHITECTURE §3.1, AI_SYSTEM_DESIGN §5).

    download (S3) → extract text (PDF / OCR / TXT) → semantic chunking
      → concept extraction (LLM) → dedup + relationship detection (LLM)
      → persist sections, concepts, edges, links (one transaction)
      → embeddings → Qdrant → status ``ready``

Idempotency (background tasks must be safely retryable):

* a Redis lock prevents two workers processing one document at once;
* only documents in ``processing`` are processed — ``ready`` is never redone;
* section ids are ``uuid5(document_id, index)`` and old sections are replaced in the
  same transaction that writes the new ones; vectors are keyed by section id;
* concepts are matched by normalised name inside a per-user advisory lock, so a
  retry (or a concurrent document) reuses concepts instead of duplicating them;
* LLM extraction results are cached per batch, so retries don't re-pay.

Failures: bad input (unreadable file, no text, OCR missing, budget exhausted) fails the
document immediately with an error code. Transient failures (LLM/S3 outages) ask the
caller to retry, and fail the document on the final attempt. If only embedding /
Qdrant fails, the document is still ``ready`` (keyword search works) and a re-index is
scheduled (ADR-005: degrade to keyword search).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy import cast, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.cost_tracker import AICallContext, AIUsageRecorder
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import PromptManager
from app.ai.providers.base import AIBudgetExceededError, LLMError
from app.config import Settings
from app.core.logging import get_logger
from app.db.models import (
    Concept,
    Course,
    Document,
    DocumentSection,
    DocumentSectionConcept,
    Subject,
)
from app.db.repositories.concept import ConceptRepository
from app.db.repositories.document import DocumentRepository
from app.integrations.qdrant import SectionVectorStore, VectorStoreError
from app.integrations.s3 import ObjectStorage, ObjectTooLargeError, StorageError
from app.services.content.chunker import Chunk, chunk_document
from app.services.content.concept_extractor import (
    CandidateConcept,
    ConceptExtractor,
    ExtractionCache,
    normalize_name,
)
from app.services.content.concept_graph import (
    ConceptGraphBuilder,
    Edge,
    ExistingConcept,
    GraphPlan,
    filter_edges,
)
from app.services.content.indexer import SectionIndexer
from app.services.content.text_extractor import (
    ExtractedText,
    ExtractionError,
    OCREngine,
    extract_text,
)

logger = get_logger(__name__)

SECTION_NAMESPACE = uuid.UUID("6f1c2b8e-4d55-4b1a-9d1e-6a2b7c3d9e01")
TASK_STATUS_TTL_SECONDS = 3600

USER_MESSAGES = {
    "AI_BUDGET_EXCEEDED": "Your daily AI budget has been reached. Try again tomorrow.",
    "LLM_UNAVAILABLE": "The AI service is unavailable right now. Please retry later.",
    "LLM_TIMEOUT": "The AI service timed out. Please retry later.",
    "LLM_RATE_LIMITED": "The AI service is busy. Please retry later.",
    "LLM_BAD_RESPONSE": "The AI service rejected the request. Please retry later.",
    "LLM_INVALID_OUTPUT": "The AI returned an unusable answer. Please retry.",
    "STORAGE_UNAVAILABLE": "The uploaded file could not be read from storage.",
    "FILE_TOO_LARGE": "The file is larger than the upload limit.",
    "INTERNAL_ERROR": "Processing failed unexpectedly.",
}


def section_id_for(document_id: uuid.UUID, index: int) -> uuid.UUID:
    return uuid.uuid5(SECTION_NAMESPACE, f"{document_id}:{index}")


class Outcome(StrEnum):
    READY = "ready"
    FAILED = "failed"
    RETRY = "retry"
    SKIPPED = "skipped"


class TaskQueue(Protocol):
    async def enqueue_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID, *, task_id: str | None = None
    ) -> str: ...

    async def enqueue_reindex(
        self, document_id: uuid.UUID, user_id: uuid.UUID, *, countdown: int = 60
    ) -> str: ...


class _DocumentGoneError(Exception):
    """The document was deleted (or reset) while it was being processed."""


class _PermanentFailureError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(slots=True)
class _Context:
    document_id: uuid.UUID
    user_id: uuid.UUID
    task_id: str | None
    subject_id: uuid.UUID | None
    course_id: uuid.UUID | None
    subject_name: str | None
    course_name: str | None
    s3_key: str
    mime_type: str


class DocumentProcessor:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        llm: LLMClient,
        recorder: AIUsageRecorder,
        prompts: PromptManager,
        ocr: OCREngine,
        vectors: SectionVectorStore | None = None,
        redis: Redis | None = None,
        queue: TaskQueue | None = None,
    ) -> None:
        self.settings = settings
        self.cfg = settings.content
        self.sessionmaker = sessionmaker
        self.storage = storage
        self.llm = llm
        self.recorder = recorder
        self.ocr = ocr
        self.vectors = vectors
        self.redis = redis
        self.queue = queue
        cache = ExtractionCache(redis, self.cfg.extraction_cache_ttl_seconds) if redis else None
        self.extractor = ConceptExtractor(llm, prompts, self.cfg, cache)
        self.graph = ConceptGraphBuilder(llm, prompts, self.cfg)

    # --- entry point -------------------------------------------------------------------------

    async def process(
        self,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        task_id: str | None = None,
        final_attempt: bool = True,
    ) -> Outcome:
        token = await self._acquire_lock(document_id)
        if token is None:
            logger.info("document already being processed", extra={"document_id": str(document_id)})
            return Outcome.SKIPPED
        trace_id: uuid.UUID | None = None
        try:
            ctx = await self._load(document_id, user_id, task_id)
            if ctx is None:
                return Outcome.SKIPPED
            trace_id = await self.recorder.start_trace(
                user_id,
                "document_ingest",
                metadata={"document_id": str(document_id), "task_id": task_id},
            )
            ai = AICallContext(
                user_id=user_id,
                trace_id=trace_id,
                purpose="document_ingest",
                metadata={"document_id": str(document_id)},
            )
            outcome = await self._run(ctx, ai)
            await self.recorder.finish_trace(trace_id, "completed")
            return outcome
        except _DocumentGoneError:
            await self._drop_vectors(user_id, document_id)
            if trace_id:
                await self.recorder.finish_trace(trace_id, "failed")
            return Outcome.SKIPPED
        except _PermanentFailureError as exc:
            await self._fail(document_id, user_id, exc.code, exc.message, trace_id)
            return Outcome.FAILED
        except (LLMError, StorageError, VectorStoreError, OperationalError, DBAPIError) as exc:
            code = self._code_for(exc)
            retryable = getattr(exc, "retryable", True) and not isinstance(
                exc, AIBudgetExceededError
            )
            if retryable and not final_attempt:
                logger.warning(
                    "document processing will retry",
                    extra={"document_id": str(document_id), "error": code},
                )
                await self._patch_metadata(document_id, {"stage": "retrying", "last_error": code})
                if trace_id:
                    await self.recorder.finish_trace(trace_id, "failed")
                return Outcome.RETRY
            await self._fail(
                document_id, user_id, code, USER_MESSAGES.get(code, str(exc)), trace_id
            )
            return Outcome.FAILED
        except Exception:
            logger.exception("document processing crashed", extra={"document_id": str(document_id)})
            await self._fail(
                document_id, user_id, "INTERNAL_ERROR", USER_MESSAGES["INTERNAL_ERROR"], trace_id
            )
            return Outcome.FAILED
        finally:
            await self._release_lock(document_id, token)

    async def reindex(
        self, document_id: uuid.UUID, user_id: uuid.UUID, *, final_attempt: bool = True
    ) -> Outcome:
        """Re-embed a ready document's sections into Qdrant (after an indexing failure)."""
        if self.vectors is None:
            return Outcome.SKIPPED
        token = await self._acquire_lock(document_id)
        if token is None:
            return Outcome.SKIPPED
        try:
            async with self.sessionmaker() as session:
                doc = await DocumentRepository(session).get_by_id(document_id, user_id)
                if doc is None or doc.processing_status != "ready":
                    return Outcome.SKIPPED
            trace_id = await self.recorder.start_trace(
                user_id, "document_reindex", metadata={"document_id": str(document_id)}
            )
            ai = AICallContext(
                user_id=user_id,
                trace_id=trace_id,
                purpose="document_reindex",
                metadata={"document_id": str(document_id)},
            )
            indexer = SectionIndexer(self.llm, self.vectors, self.sessionmaker, self.cfg)
            try:
                await indexer.index_document(document_id, user_id, ai)
            except (LLMError, VectorStoreError) as exc:
                await self.recorder.finish_trace(trace_id, "failed")
                if getattr(exc, "retryable", True) and not final_attempt:
                    return Outcome.RETRY
                await self._set_embedding_status(document_id, "failed")
                return Outcome.FAILED
            await self.recorder.finish_trace(trace_id, "completed")
            await self._set_embedding_status(document_id, "complete")
            return Outcome.READY
        finally:
            await self._release_lock(document_id, token)

    async def _set_embedding_status(self, document_id: uuid.UUID, status: str) -> None:
        async with self.sessionmaker() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(
                    processing_metadata=func.coalesce(
                        Document.processing_metadata, text("'{}'::jsonb")
                    ).op("||")(cast({"embedding_status": status}, JSONB))
                )
            )
            await session.commit()

    # --- pipeline ------------------------------------------------------------------------------

    async def _run(self, ctx: _Context, ai: AICallContext) -> Outcome:
        await self._stage(ctx, "downloading", 0.05)
        try:
            data = await self.storage.get_bytes(ctx.s3_key, max_bytes=self.cfg.upload_max_bytes)
        except ObjectTooLargeError as exc:
            raise _PermanentFailureError("FILE_TOO_LARGE", USER_MESSAGES["FILE_TOO_LARGE"]) from exc

        await self._stage(ctx, "extracting_text", 0.1)
        try:
            extracted: ExtractedText = await asyncio.to_thread(
                extract_text, data, ctx.mime_type, self.cfg, self.ocr
            )
        except ExtractionError as exc:
            raise _PermanentFailureError(exc.code, exc.message) from exc
        del data

        await self._stage(ctx, "chunking", 0.2)
        chunks = chunk_document(
            extracted.pages,
            min_tokens=self.cfg.chunk_min_tokens,
            max_tokens=self.cfg.chunk_max_tokens,
            overlap_tokens=self.cfg.chunk_overlap_tokens,
        )
        if not chunks:
            raise _PermanentFailureError("NO_TEXT", "No readable text was found in the document")

        await self._stage(ctx, "extracting_concepts", 0.3)
        candidates = await self.extractor.extract(
            chunks, ai, subject_name=ctx.subject_name, course_name=ctx.course_name
        )

        await self._stage(ctx, "linking_concepts", 0.7)
        async with self.sessionmaker() as session:
            existing = _existing(
                await ConceptRepository(session).in_scope(ctx.user_id, ctx.subject_id)
            )
        plan = await self.graph.plan(
            candidates, _rank_context(existing, candidates), ai, subject_name=ctx.subject_name
        )

        await self._stage(ctx, "saving", 0.8)
        stats = await self._persist(ctx, extracted, chunks, candidates, plan)

        await self._stage(ctx, "indexing", 0.9)
        embedding_status = await self._index(ctx, ai)
        await self._finalize(ctx, stats | {"embedding_status": embedding_status})
        logger.info(
            "document ready",
            extra={"document_id": str(ctx.document_id), "chunks": len(chunks), **stats},
        )
        return Outcome.READY

    async def _load(
        self, document_id: uuid.UUID, user_id: uuid.UUID, task_id: str | None
    ) -> _Context | None:
        async with self.sessionmaker() as session:
            doc = await DocumentRepository(session).get_by_id(document_id, user_id)
            if doc is None or doc.processing_status != "processing":
                return None
            subject_name = (
                await session.scalar(select(Subject.name).where(Subject.id == doc.subject_id))
                if doc.subject_id
                else None
            )
            course_name = (
                await session.scalar(select(Course.name).where(Course.id == doc.course_id))
                if doc.course_id
                else None
            )
            return _Context(
                document_id=doc.id,
                user_id=user_id,
                task_id=task_id,
                subject_id=doc.subject_id,
                course_id=doc.course_id,
                subject_name=subject_name,
                course_name=course_name,
                s3_key=doc.s3_key,
                mime_type=doc.mime_type,
            )

    async def _persist(
        self,
        ctx: _Context,
        extracted: ExtractedText,
        chunks: list[Chunk],
        candidates: dict[str, CandidateConcept],
        plan: GraphPlan,
    ) -> dict[str, Any]:
        async with self.sessionmaker() as session, session.begin():
            # Serialise concept writes per user so concurrent documents can't duplicate.
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(func.hashtextextended(f"concepts:{ctx.user_id}", 0))
                )
            )
            doc = await DocumentRepository(session).get_for_update(ctx.document_id, ctx.user_id)
            if doc is None or doc.processing_status != "processing":
                raise _DocumentGoneError

            repo = ConceptRepository(session)
            in_scope = {
                normalize_name(c.name): c for c in await repo.in_scope(ctx.user_id, ctx.subject_id)
            }
            ids = dict(plan.concept_ids)
            remap: dict[uuid.UUID, uuid.UUID] = {}
            for key, cid in plan.concept_ids.items():
                if (
                    key not in plan.merged and key in in_scope
                ):  # created concurrently / by a prior attempt
                    remap[cid] = ids[key] = in_scope[key].id
            existing_ids = {c.id: c for c in in_scope.values()}

            await session.execute(
                delete(DocumentSection).where(DocumentSection.document_id == doc.id)
            )

            created = merged = 0
            created_ids: set[uuid.UUID] = set()
            for key, cand in candidates.items():
                cid = ids[key]
                if cid in created_ids:
                    continue
                if cid in existing_ids:
                    concept = existing_ids[cid]
                    meta = dict(concept.metadata_ or {})
                    sources = list(
                        dict.fromkeys([*meta.get("source_document_ids", []), str(doc.id)])
                    )
                    concept.metadata_ = meta | {"source_document_ids": sources}
                    if not concept.description and cand.description:
                        concept.description = cand.description
                    merged += 1
                else:
                    session.add(
                        Concept(
                            id=cid,
                            user_id=ctx.user_id,
                            name=cand.name[:200],
                            description=cand.description or None,
                            difficulty_estimate=cand.difficulty,
                            subject_id=ctx.subject_id,
                            metadata_={
                                "origin": "extracted",
                                "source_document_ids": [str(doc.id)],
                                "aliases": sorted(cand.aliases)[:10],
                            },
                        )
                    )
                    created_ids.add(cid)
                    created += 1

            session.add_all(
                DocumentSection(
                    id=section_id_for(doc.id, chunk.index),
                    document_id=doc.id,
                    section_index=chunk.index,
                    content=chunk.content,
                    page_numbers=chunk.page_numbers or None,
                    heading=chunk.heading[:500] if chunk.heading else None,
                    token_count=chunk.token_count,
                    metadata_=chunk.metadata,
                )
                for chunk in chunks
            )
            await session.flush()

            links: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
            for key, cand in candidates.items():
                for index, relevance in cand.sections.items():
                    pair = (section_id_for(doc.id, index), ids[key])
                    links[pair] = max(links.get(pair, 0.0), relevance)
            if links:
                await session.execute(
                    insert(DocumentSectionConcept)
                    .values(
                        [
                            {"document_section_id": s, "concept_id": c, "relevance_score": r}
                            for (s, c), r in links.items()
                        ]
                    )
                    .on_conflict_do_nothing()
                )

            edges = [
                Edge(
                    remap.get(e.source, e.source), remap.get(e.target, e.target), e.type, e.strength
                )
                for e in plan.edges
            ]
            accepted, dropped = filter_edges(edges, await repo.edges_for_user(ctx.user_id))
            await repo.add_relationships(
                [
                    {
                        "source_concept_id": e.source,
                        "target_concept_id": e.target,
                        "relationship_type": e.type,
                        "strength": round(e.strength, 3),
                    }
                    for e in accepted
                ]
            )

            stats: dict[str, Any] = {
                "page_count": extracted.page_count,
                "chunk_count": len(chunks),
                "concept_count": len(set(ids.values())),
                "new_concept_count": created,
                "merged_concept_count": merged,
                "relationship_count": len(accepted),
                "dropped_cyclic_edges": len(dropped),
                "relationship_detection": plan.relationship_detection,
                "ocr_pages": extracted.ocr_pages,
            }
            warnings = []
            if extracted.ocr_unavailable_pages:
                warnings.append(
                    f"{len(extracted.ocr_unavailable_pages)} page(s) had no text layer "
                    "and OCR is unavailable"
                )
            if plan.relationship_detection != "complete":
                warnings.append("Concept relationships could not be detected")
            stats["warnings"] = warnings
            doc.processing_metadata = (doc.processing_metadata or {}) | stats
        return stats

    async def _index(self, ctx: _Context, ai: AICallContext) -> str:
        if self.vectors is None:
            return "skipped"
        indexer = SectionIndexer(self.llm, self.vectors, self.sessionmaker, self.cfg)
        try:
            await indexer.index_document(ctx.document_id, ctx.user_id, ai)
        except (LLMError, VectorStoreError) as exc:
            logger.warning(
                "indexing failed; document searchable by keyword only",
                extra={"document_id": str(ctx.document_id), "error": type(exc).__name__},
            )
            if self.queue is not None and getattr(exc, "retryable", True):
                try:
                    await self.queue.enqueue_reindex(ctx.document_id, ctx.user_id, countdown=60)
                except Exception:
                    logger.exception("could not schedule re-index")
            return "failed"
        return "complete"

    async def _finalize(self, ctx: _Context, stats: dict[str, Any]) -> None:
        async with self.sessionmaker() as session, session.begin():
            doc = await DocumentRepository(session).get_for_update(ctx.document_id, ctx.user_id)
            if doc is None or doc.processing_status != "processing":
                raise _DocumentGoneError
            doc.processing_status = "ready"
            doc.processed_at = func.now()
            meta = {
                k: v
                for k, v in (doc.processing_metadata or {}).items()
                if k not in ("error", "last_error")
            }
            doc.processing_metadata = meta | stats | {"stage": "complete", "progress": 1.0}
        await self._task_status(ctx.task_id, ctx.document_id, "ready", "complete", 1.0)

    # --- failure / progress helpers ------------------------------------------------------------

    @staticmethod
    def _code_for(exc: Exception) -> str:
        if isinstance(exc, LLMError):
            return exc.code
        if isinstance(exc, StorageError):
            return "STORAGE_UNAVAILABLE"
        return "INTERNAL_ERROR"

    async def _fail(
        self,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        code: str,
        message: str,
        trace_id: uuid.UUID | None,
    ) -> None:
        logger.warning(
            "document processing failed", extra={"document_id": str(document_id), "error": code}
        )
        try:
            async with self.sessionmaker() as session, session.begin():
                doc = await DocumentRepository(session).get_for_update(document_id, user_id)
                if doc is not None and doc.processing_status == "processing":
                    linked = (
                        await session.scalars(
                            select(DocumentSectionConcept.concept_id)
                            .join(
                                DocumentSection,
                                DocumentSection.id == DocumentSectionConcept.document_section_id,
                            )
                            .where(DocumentSection.document_id == document_id)
                        )
                    ).all()
                    await session.execute(
                        delete(DocumentSection).where(DocumentSection.document_id == document_id)
                    )
                    await ConceptRepository(session).delete_orphaned_extracted(
                        user_id, list(set(linked))
                    )
                    doc.processing_status = "failed"
                    doc.processing_metadata = (doc.processing_metadata or {}) | {
                        "stage": "failed",
                        "error": {"code": code, "message": message},
                    }
                    task_id = (doc.processing_metadata or {}).get("task_id")
                else:
                    task_id = None
        except Exception:
            logger.exception(
                "could not mark document failed", extra={"document_id": str(document_id)}
            )
            task_id = None
        await self._drop_vectors(user_id, document_id)
        await self._task_status(task_id, document_id, "failed", "failed", None)
        if trace_id:
            await self.recorder.finish_trace(trace_id, "failed")

    async def _drop_vectors(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        if self.vectors is None:
            return
        try:
            await self.vectors.delete_document(user_id, document_id)
        except VectorStoreError:
            logger.warning("could not remove vectors", extra={"document_id": str(document_id)})

    async def _stage(self, ctx: _Context, stage: str, progress: float) -> None:
        await self._patch_metadata(ctx.document_id, {"stage": stage, "progress": progress})
        await self._task_status(ctx.task_id, ctx.document_id, "processing", stage, progress)

    async def _patch_metadata(self, document_id: uuid.UUID, patch: dict[str, Any]) -> None:
        async with self.sessionmaker() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id, Document.processing_status == "processing")
                .values(
                    processing_metadata=func.coalesce(
                        Document.processing_metadata, text("'{}'::jsonb")
                    ).op("||")(cast(patch, JSONB))
                )
            )
            await session.commit()

    async def _task_status(
        self,
        task_id: str | None,
        document_id: uuid.UUID,
        status: str,
        stage: str,
        progress: float | None,
    ) -> None:
        if not task_id or self.redis is None:
            return
        payload = {
            "document_id": str(document_id),
            "status": status,
            "stage": stage,
            "progress": progress,
        }
        try:
            await self.redis.set(
                f"task:{task_id}:status", json.dumps(payload), ex=TASK_STATUS_TTL_SECONDS
            )
        except Exception as exc:
            logger.warning("task status unavailable", extra={"error": type(exc).__name__})

    # --- lock ------------------------------------------------------------------------------------

    async def _acquire_lock(self, document_id: uuid.UUID) -> str | None:
        token = uuid.uuid4().hex
        if self.redis is None:
            return token
        try:
            ok = await self.redis.set(
                f"lock:document:{document_id}",
                token,
                nx=True,
                ex=self.cfg.processing_lock_ttl_seconds,
            )
        except Exception as exc:
            logger.warning("document lock unavailable", extra={"error": type(exc).__name__})
            return token
        return token if ok else None

    async def _release_lock(self, document_id: uuid.UUID, token: str) -> None:
        if self.redis is None:
            return
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) end return 0"
        )
        try:
            await self.redis.eval(script, 1, f"lock:document:{document_id}", token)  # type: ignore[misc]
        except Exception as exc:
            logger.warning("document lock release failed", extra={"error": type(exc).__name__})


# --- helpers ---------------------------------------------------------------------------------


def _existing(concepts: Any) -> list[ExistingConcept]:
    return [ExistingConcept(c.id, normalize_name(c.name), c.name, c.description) for c in concepts]


def _rank_context(
    existing: list[ExistingConcept], candidates: dict[str, CandidateConcept]
) -> list[ExistingConcept]:
    """Order existing concepts by word overlap with the new ones (most relevant first) so
    the LLM context — capped in size — holds the likeliest duplicates/relations."""
    words: dict[str, int] = defaultdict(int)
    for key in candidates:
        for w in key.split():
            if len(w) > 2:
                words[w] += 1

    def score(e: ExistingConcept) -> int:
        return sum(words.get(w, 0) for w in e.key.split())

    return sorted(existing, key=lambda e: -score(e))
