"""Document data access (user-scoped)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, func, select

from app.db.models import Concept, Document, DocumentSection, DocumentSectionConcept
from app.db.repositories.base import BaseRepository, Page, PageRequest


@dataclass(frozen=True, slots=True)
class LinkedConcept:
    id: uuid.UUID
    name: str
    section_count: int


class DocumentRepository(BaseRepository[Document]):
    model = Document
    default_order_by = (Document.uploaded_at.desc(), Document.id)

    async def list_documents(
        self,
        user_id: uuid.UUID,
        page: PageRequest,
        *,
        status: str | None = None,
        subject_id: uuid.UUID | None = None,
        course_id: uuid.UUID | None = None,
    ) -> Page[Document]:
        filters: list[ColumnElement[bool]] = []
        if status:
            filters.append(Document.processing_status == status)
        if subject_id:
            filters.append(Document.subject_id == subject_id)
        if course_id:
            filters.append(Document.course_id == course_id)
        return await self.paginate(user_id, page, *filters)

    async def get_for_update(self, document_id: uuid.UUID, user_id: uuid.UUID) -> Document | None:
        stmt = (
            self._owned(user_id)
            .where(Document.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(stmt)).one_or_none()

    async def section_count(self, document_id: uuid.UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count(DocumentSection.id)).where(
                    DocumentSection.document_id == document_id
                )
            )
            or 0
        )

    async def linked_concepts(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[LinkedConcept]:
        stmt = (
            select(Concept.id, Concept.name, func.count(DocumentSectionConcept.id))
            .join(DocumentSectionConcept, DocumentSectionConcept.concept_id == Concept.id)
            .join(DocumentSection, DocumentSection.id == DocumentSectionConcept.document_section_id)
            .where(DocumentSection.document_id == document_id, Concept.user_id == user_id)
            .group_by(Concept.id, Concept.name)
            .order_by(func.count(DocumentSectionConcept.id).desc(), Concept.name)
        )
        rows = (await self.session.execute(stmt)).all()
        return [LinkedConcept(r[0], r[1], int(r[2])) for r in rows]
