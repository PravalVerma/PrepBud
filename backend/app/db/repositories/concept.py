"""Concept graph data access (user-scoped). Every query is filtered by ``user_id``;
relationships are only followed between concepts the user owns."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import (
    ColumnElement,
    and_,
    delete,
    distinct,
    exists,
    func,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from app.db.models import (
    Concept,
    ConceptRelationship,
    Document,
    DocumentSection,
    DocumentSectionConcept,
    Misconception,
    Question,
    StudentConceptMastery,
    StudentMisconception,
)
from app.db.repositories.base import BaseRepository, Page, PageRequest

SortField = Literal["name", "difficulty_estimate", "mastery_level", "created_at"]
ENGLISH: ColumnElement[Any] = literal_column("'english'")
RELATED_TYPES = ("related", "generalisation", "specialisation")
# Viewed from the other end of an edge, generalisation and specialisation swap.
_FLIP = {"generalisation": "specialisation", "specialisation": "generalisation"}


def concept_tsvector() -> ColumnElement[Any]:
    """Matches the GIN index expression on concepts (DATA_MODEL §3.7) so it is used."""
    return func.to_tsvector(
        ENGLISH,
        Concept.name.op("||")(literal_column("' '")).op("||")(
            func.coalesce(Concept.description, literal_column("''"))
        ),
    )


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True, slots=True)
class ConceptRow:
    concept: Concept
    mastery: StudentConceptMastery | None
    prerequisite_count: int
    document_count: int


@dataclass(frozen=True, slots=True)
class ConceptRef:
    id: uuid.UUID
    name: str
    mastery_level: float
    relationship: str | None = None


@dataclass(frozen=True, slots=True)
class MisconceptionRef:
    id: uuid.UUID
    name: str
    status: str


@dataclass(frozen=True, slots=True)
class DocumentRef:
    id: uuid.UUID
    title: str
    section_count: int


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: uuid.UUID
    name: str
    mastery: float


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: uuid.UUID
    target: uuid.UUID
    type: str


class ConceptRepository(BaseRepository[Concept]):
    model = Concept
    default_order_by = (Concept.name, Concept.id)

    # --- list ---------------------------------------------------------------------------

    async def list_with_stats(
        self,
        user_id: uuid.UUID,
        page: PageRequest,
        *,
        subject_id: uuid.UUID | None = None,
        chapter_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
        mastery_below: float | None = None,
        search: str | None = None,
        sort: SortField = "name",
        order: Literal["asc", "desc"] = "asc",
    ) -> Page[ConceptRow]:
        scm = aliased(StudentConceptMastery)
        prerequisite_count = (
            select(func.count(ConceptRelationship.id))
            .where(
                ConceptRelationship.target_concept_id == Concept.id,
                ConceptRelationship.relationship_type == "prerequisite",
            )
            .correlate(Concept)
            .scalar_subquery()
        )
        document_count = (
            select(func.count(distinct(DocumentSection.document_id)))
            .select_from(DocumentSectionConcept)
            .join(DocumentSection, DocumentSection.id == DocumentSectionConcept.document_section_id)
            .where(DocumentSectionConcept.concept_id == Concept.id)
            .correlate(Concept)
            .scalar_subquery()
        )
        mastery_level = func.coalesce(scm.mastery_level, 0.0)
        filters: list[ColumnElement[bool]] = [Concept.user_id == user_id]
        if subject_id:
            filters.append(Concept.subject_id == subject_id)
        if chapter_id:
            filters.append(Concept.chapter_id == chapter_id)
        if document_id:
            filters.append(
                exists()
                .where(
                    DocumentSectionConcept.concept_id == Concept.id,
                    DocumentSectionConcept.document_section_id == DocumentSection.id,
                    DocumentSection.document_id == document_id,
                )
                .correlate(Concept)
            )
        if mastery_below is not None:
            filters.append(mastery_level < mastery_below)

        order_by: list[Any] = []
        if search:
            query = func.websearch_to_tsquery(ENGLISH, search)
            vector = concept_tsvector()
            filters.append(
                or_(
                    vector.op("@@")(query),
                    Concept.name.ilike(f"%{escape_like(search)}%", escape="\\"),
                )
            )
            order_by.append(func.ts_rank(vector, query).desc())
        sort_col: Any = {
            "name": Concept.name,
            "difficulty_estimate": Concept.difficulty_estimate,
            "mastery_level": mastery_level,
            "created_at": Concept.created_at,
        }[sort]
        order_by += [sort_col.desc() if order == "desc" else sort_col.asc(), Concept.id]

        base = (
            select(Concept, scm, prerequisite_count, document_count)
            .outerjoin(scm, and_(scm.concept_id == Concept.id, scm.user_id == user_id))
            .where(*filters)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(
                select(Concept.id)
                .outerjoin(scm, and_(scm.concept_id == Concept.id, scm.user_id == user_id))
                .where(*filters)
                .subquery()
            )
        )
        rows = (
            await self.session.execute(
                base.order_by(*order_by).offset(page.offset).limit(page.per_page)
            )
        ).all()
        items = [ConceptRow(r[0], r[1], int(r[2]), int(r[3])) for r in rows]
        return Page(items=items, total=total or 0, page=page.page, per_page=page.per_page)

    async def mastery(
        self, concept_id: uuid.UUID, user_id: uuid.UUID
    ) -> StudentConceptMastery | None:
        return await self.session.scalar(
            select(StudentConceptMastery).where(
                StudentConceptMastery.concept_id == concept_id,
                StudentConceptMastery.user_id == user_id,
            )
        )

    # --- detail ---------------------------------------------------------------------------

    def _with_mastery(self, user_id: uuid.UUID) -> tuple[Any, Any]:
        scm = aliased(StudentConceptMastery)
        return scm, and_(scm.concept_id == Concept.id, scm.user_id == user_id)

    async def prerequisites(self, concept_id: uuid.UUID, user_id: uuid.UUID) -> list[ConceptRef]:
        scm, on = self._with_mastery(user_id)
        rows = await self.session.execute(
            select(Concept.id, Concept.name, func.coalesce(scm.mastery_level, 0.0))
            .join(ConceptRelationship, ConceptRelationship.source_concept_id == Concept.id)
            .outerjoin(scm, on)
            .where(
                ConceptRelationship.target_concept_id == concept_id,
                ConceptRelationship.relationship_type == "prerequisite",
                Concept.user_id == user_id,
            )
            .order_by(ConceptRelationship.strength.desc(), Concept.name)
        )
        return [ConceptRef(r[0], r[1], float(r[2])) for r in rows]

    async def dependents(self, concept_id: uuid.UUID, user_id: uuid.UUID) -> list[ConceptRef]:
        scm, on = self._with_mastery(user_id)
        rows = await self.session.execute(
            select(Concept.id, Concept.name, func.coalesce(scm.mastery_level, 0.0))
            .join(ConceptRelationship, ConceptRelationship.target_concept_id == Concept.id)
            .outerjoin(scm, on)
            .where(
                ConceptRelationship.source_concept_id == concept_id,
                ConceptRelationship.relationship_type == "prerequisite",
                Concept.user_id == user_id,
            )
            .order_by(Concept.name)
        )
        return [ConceptRef(r[0], r[1], float(r[2])) for r in rows]

    async def related(self, concept_id: uuid.UUID, user_id: uuid.UUID) -> list[ConceptRef]:
        """Non-prerequisite neighbours, typed from this concept's point of view."""
        scm, on = self._with_mastery(user_id)
        rel = ConceptRelationship
        is_source = rel.source_concept_id == concept_id
        other_id = func.coalesce(
            func.nullif(rel.target_concept_id, concept_id), rel.source_concept_id
        )
        rows = await self.session.execute(
            select(
                Concept.id,
                Concept.name,
                func.coalesce(scm.mastery_level, 0.0),
                rel.relationship_type,
                is_source,
            )
            .select_from(rel)
            .join(Concept, Concept.id == other_id)
            .outerjoin(scm, on)
            .where(
                or_(rel.source_concept_id == concept_id, rel.target_concept_id == concept_id),
                rel.relationship_type.in_(RELATED_TYPES),
                Concept.user_id == user_id,
            )
            .order_by(rel.strength.desc(), Concept.name)
        )
        out: dict[uuid.UUID, ConceptRef] = {}
        for cid, name, level, kind, outgoing in rows:
            # Edge "A generalisation B": A is the general form of B. Seen from A, B is a
            # specialisation; seen from B, A is a generalisation.
            relationship = _FLIP.get(kind, kind) if outgoing else kind
            out.setdefault(cid, ConceptRef(cid, name, float(level), relationship))
        return list(out.values())

    async def misconceptions(
        self, concept_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[MisconceptionRef]:
        rows = await self.session.execute(
            select(Misconception.id, Misconception.name, StudentMisconception.status)
            .join(
                StudentMisconception,
                and_(
                    StudentMisconception.misconception_id == Misconception.id,
                    StudentMisconception.user_id == user_id,
                ),
            )
            .where(Misconception.concept_id == concept_id)
            .order_by(StudentMisconception.detected_at.desc())
        )
        return [MisconceptionRef(r[0], r[1], r[2] or "active") for r in rows]

    async def documents(self, concept_id: uuid.UUID, user_id: uuid.UUID) -> list[DocumentRef]:
        rows = await self.session.execute(
            select(Document.id, Document.title, func.count(distinct(DocumentSection.id)))
            .join(DocumentSection, DocumentSection.document_id == Document.id)
            .join(
                DocumentSectionConcept,
                DocumentSectionConcept.document_section_id == DocumentSection.id,
            )
            .where(DocumentSectionConcept.concept_id == concept_id, Document.user_id == user_id)
            .group_by(Document.id, Document.title)
            .order_by(Document.title)
        )
        return [DocumentRef(r[0], r[1], int(r[2])) for r in rows]

    # --- graph ---------------------------------------------------------------------------------

    async def neighbourhood(
        self, concept_id: uuid.UUID, user_id: uuid.UUID, *, depth: int, max_nodes: int = 150
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        src, tgt = aliased(Concept), aliased(Concept)
        rel = ConceptRelationship
        seen: set[uuid.UUID] = {concept_id}
        frontier: set[uuid.UUID] = {concept_id}
        edges: dict[uuid.UUID, GraphEdge] = {}
        for _ in range(depth):
            if not frontier or len(seen) >= max_nodes:
                break
            rows = await self.session.execute(
                select(rel.id, rel.source_concept_id, rel.target_concept_id, rel.relationship_type)
                .join(src, src.id == rel.source_concept_id)
                .join(tgt, tgt.id == rel.target_concept_id)
                .where(
                    src.user_id == user_id,
                    tgt.user_id == user_id,
                    or_(rel.source_concept_id.in_(frontier), rel.target_concept_id.in_(frontier)),
                )
            )
            nxt: set[uuid.UUID] = set()
            for eid, s, t, kind in rows:
                for node in (s, t):
                    if node not in seen and len(seen) < max_nodes:
                        seen.add(node)
                        nxt.add(node)
                if s in seen and t in seen:
                    edges[eid] = GraphEdge(s, t, kind)
            frontier = nxt

        scm, on = self._with_mastery(user_id)
        node_rows = await self.session.execute(
            select(Concept.id, Concept.name, func.coalesce(scm.mastery_level, 0.0))
            .outerjoin(scm, on)
            .where(Concept.id.in_(seen), Concept.user_id == user_id)
            .order_by(Concept.name)
        )
        nodes = [GraphNode(r[0], r[1], float(r[2])) for r in node_rows]
        return nodes, list(edges.values())

    # --- pipeline support ---------------------------------------------------------------------

    async def in_scope(self, user_id: uuid.UUID, subject_id: uuid.UUID | None) -> Sequence[Concept]:
        """Concepts a new document's concepts may merge with: same subject (or none)."""
        scope = Concept.subject_id == subject_id if subject_id else Concept.subject_id.is_(None)
        return (
            await self.session.scalars(
                select(Concept)
                .where(Concept.user_id == user_id, scope)
                .order_by(Concept.updated_at.desc())
            )
        ).all()

    async def edges_for_user(self, user_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        rows = await self.session.execute(
            select(
                ConceptRelationship.source_concept_id,
                ConceptRelationship.target_concept_id,
                ConceptRelationship.relationship_type,
            )
            .join(Concept, Concept.id == ConceptRelationship.source_concept_id)
            .where(Concept.user_id == user_id)
        )
        return [(r[0], r[1], r[2]) for r in rows]

    async def add_relationships(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(
                insert(ConceptRelationship).values(rows).on_conflict_do_nothing()
            )

    async def delete_orphaned_extracted(
        self, user_id: uuid.UUID, concept_ids: Sequence[uuid.UUID]
    ) -> int:
        """Delete extracted concepts no longer backed by any document and carrying no
        learning data (mastery records or questions). Returns the number deleted."""
        if not concept_ids:
            return 0
        result = await self.session.execute(
            delete(Concept)
            .where(
                Concept.user_id == user_id,
                Concept.id.in_(concept_ids),
                Concept.metadata_["origin"].astext == "extracted",
                ~exists().where(DocumentSectionConcept.concept_id == Concept.id),
                ~exists().where(StudentConceptMastery.concept_id == Concept.id),
                ~exists().where(Question.concept_id == Concept.id),
            )
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)
