"""Embedding generation + Qdrant indexing for a document's sections.

Idempotent: point ids are the section ids, so indexing twice overwrites. Used by the
document pipeline and by the stand-alone re-index task (embedding_tasks).
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.cost_tracker import AICallContext
from app.ai.llm_client import LLMClient
from app.config import ContentProcessingSettings
from app.db.models import Document, DocumentSection, DocumentSectionConcept
from app.integrations.qdrant import SectionPoint, SectionVectorStore

PREVIEW_CHARS = 300


class SectionIndexer:
    def __init__(
        self,
        llm: LLMClient,
        vectors: SectionVectorStore,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: ContentProcessingSettings,
    ) -> None:
        self.llm = llm
        self.vectors = vectors
        self.sessionmaker = sessionmaker
        self.settings = settings

    async def index_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID, ctx: AICallContext
    ) -> int:
        """Embed every section of the document and upsert it into Qdrant.

        Returns the number of indexed sections. Raises `LLMError` / `VectorStoreError`.
        """
        async with self.sessionmaker() as session:
            doc = await session.scalar(
                select(Document).where(Document.id == document_id, Document.user_id == user_id)
            )
            if doc is None:
                return 0
            sections = (
                await session.scalars(
                    select(DocumentSection)
                    .where(DocumentSection.document_id == document_id)
                    .order_by(DocumentSection.section_index)
                )
            ).all()
            links = await session.execute(
                select(
                    DocumentSectionConcept.document_section_id, DocumentSectionConcept.concept_id
                )
                .join(
                    DocumentSection,
                    DocumentSection.id == DocumentSectionConcept.document_section_id,
                )
                .where(DocumentSection.document_id == document_id)
            )
            concepts_by_section: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
            for section_id, concept_id in links:
                concepts_by_section[section_id].append(concept_id)
            subject_id, course_id = doc.subject_id, doc.course_id

        if not sections:
            return 0
        size = max(1, self.settings.embedding_batch_size)
        embed_ctx = ctx.for_purpose("document_embedding")
        points: list[SectionPoint] = []
        for start in range(0, len(sections), size):
            batch = sections[start : start + size]
            vectors = await self.llm.embed([s.content for s in batch], embed_ctx)
            points += [
                SectionPoint(
                    section_id=s.id,
                    vector=vector,
                    user_id=user_id,
                    document_id=document_id,
                    section_index=s.section_index,
                    subject_id=subject_id,
                    course_id=course_id,
                    concept_ids=sorted(concepts_by_section.get(s.id, []), key=str),
                    heading=s.heading,
                    content_preview=s.content[:PREVIEW_CHARS],
                    token_count=s.token_count or 0,
                )
                for s, vector in zip(batch, vectors, strict=True)
            ]
        await self.vectors.upsert_sections(points)

        async with self.sessionmaker() as session:
            await session.execute(
                update(DocumentSection)
                .where(DocumentSection.document_id == document_id)
                .values(embedding_id=DocumentSection.id.cast(DocumentSection.embedding_id.type))
            )
            await session.commit()
        return len(points)
