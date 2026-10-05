"""Qdrant vector store for document-section embeddings (DATA_MODEL §4.1, ADR-005).

Point id == `document_sections.id`, so re-indexing a document overwrites its points
instead of duplicating them. **Every search and delete is filtered by user_id**
(data isolation applies to vectors too).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

KEYWORD_FIELDS = ("user_id", "document_id", "subject_id", "course_id", "concept_ids")


class VectorStoreError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SectionPoint:
    section_id: uuid.UUID
    vector: list[float]
    user_id: uuid.UUID
    document_id: uuid.UUID
    section_index: int
    subject_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    concept_ids: list[uuid.UUID] = field(default_factory=list)
    heading: str | None = None
    content_preview: str = ""
    token_count: int = 0

    def payload(self) -> dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "document_id": str(self.document_id),
            "subject_id": str(self.subject_id) if self.subject_id else None,
            "course_id": str(self.course_id) if self.course_id else None,
            "concept_ids": [str(c) for c in self.concept_ids],
            "section_index": self.section_index,
            "heading": self.heading,
            "content_preview": self.content_preview,
            "token_count": self.token_count,
        }


@dataclass(frozen=True, slots=True)
class VectorHit:
    section_id: uuid.UUID
    score: float


def _match(key: str, value: object) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))


class SectionVectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str, dimensions: int) -> None:
        self.client = client
        self.collection = collection
        self.dimensions = dimensions
        self._ready = False

    @classmethod
    def from_settings(cls, settings: Settings) -> SectionVectorStore | None:
        """``None`` when Qdrant isn't configured (search then degrades to keyword)."""
        if not settings.qdrant_url:
            return None
        client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() or None,
            timeout=int(settings.qdrant_timeout_seconds),
        )
        return cls(client, settings.qdrant_sections_collection, settings.embedding_dimensions)

    async def aclose(self) -> None:
        await self.client.close()

    async def ensure_collection(self) -> None:
        """Create the collection + payload indexes if missing (idempotent)."""
        if self._ready:
            return
        try:
            if not await self.client.collection_exists(self.collection):
                await self.client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(
                        size=self.dimensions, distance=models.Distance.COSINE
                    ),
                )
                for name in KEYWORD_FIELDS:
                    await self.client.create_payload_index(
                        self.collection, name, models.PayloadSchemaType.KEYWORD
                    )
                await self.client.create_payload_index(
                    self.collection, "section_index", models.PayloadSchemaType.INTEGER
                )
            else:
                info = await self.client.get_collection(self.collection)
                vectors = info.config.params.vectors
                size = vectors.size if isinstance(vectors, models.VectorParams) else None
                if size is not None and size != self.dimensions:
                    raise VectorStoreError(
                        f"Collection {self.collection!r} has vector size {size}, "
                        f"embedding task produces {self.dimensions}"
                    )
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"Qdrant unavailable: {type(exc).__name__}") from exc
        self._ready = True

    async def upsert_sections(self, points: list[SectionPoint]) -> None:
        if not points:
            return
        await self.ensure_collection()
        try:
            await self.client.upsert(
                self.collection,
                points=[
                    models.PointStruct(id=str(p.section_id), vector=p.vector, payload=p.payload())
                    for p in points
                ],
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Qdrant upsert failed: {type(exc).__name__}") from exc

    async def delete_document(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        await self.ensure_collection()
        try:
            await self.client.delete(
                self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[_match("user_id", user_id), _match("document_id", document_id)]
                    )
                ),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Qdrant delete failed: {type(exc).__name__}") from exc

    async def search(
        self,
        user_id: uuid.UUID,
        vector: list[float],
        *,
        limit: int,
        subject_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
        concept_id: uuid.UUID | None = None,
    ) -> list[VectorHit]:
        must = [_match("user_id", user_id)]
        if subject_id:
            must.append(_match("subject_id", subject_id))
        if document_id:
            must.append(_match("document_id", document_id))
        if concept_id:
            must.append(_match("concept_ids", concept_id))
        await self.ensure_collection()
        try:
            result = await self.client.query_points(
                self.collection,
                query=vector,
                query_filter=models.Filter(must=must),
                limit=limit,
                with_payload=False,
            )
        except Exception as exc:
            raise VectorStoreError(f"Qdrant search failed: {type(exc).__name__}") from exc
        return [VectorHit(section_id=uuid.UUID(str(p.id)), score=p.score) for p in result.points]

    async def count_document_points(self, user_id: uuid.UUID, document_id: uuid.UUID) -> int:
        await self.ensure_collection()
        result = await self.client.count(
            self.collection,
            count_filter=models.Filter(
                must=[_match("user_id", user_id), _match("document_id", document_id)]
            ),
            exact=True,
        )
        return result.count
