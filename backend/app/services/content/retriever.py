"""Hybrid content retrieval (ARCHITECTURE §2.4, LEARNING_ENGINE §9.1).

* Semantic: embed the query, search Qdrant (always filtered by user_id).
* Keyword: PostgreSQL full-text search over document sections (``websearch_to_tsquery``,
  served by the ``idx_doc_sections_content_fts`` GIN index).
* Merge: reciprocal-rank fusion (RRF, k=60) — robust to the two engines' incomparable
  score scales; a section found by both ranks above one found by either alone.

If the vector side is unavailable (not configured, Qdrant down, embedding failure),
hybrid search degrades to keyword results and reports ``degraded=True`` (ADR-005).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Text, case, cast, exists, func, literal_column, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.cost_tracker import AICallContext, AIUsageRecorder
from app.ai.llm_client import LLMClient
from app.ai.providers.base import AIBudgetExceededError, LLMError
from app.core.logging import get_logger
from app.db.models import Document, DocumentSection, DocumentSectionConcept
from app.integrations.qdrant import SectionVectorStore, VectorStoreError

logger = get_logger(__name__)

RRF_K = 60
ENGLISH: ColumnElement[Any] = literal_column("'english'")
SNIPPET_CHARS = 320
HEADLINE_OPTIONS = (
    'StartSel=**, StopSel=**, MaxWords=45, MinWords=20, MaxFragments=2, FragmentDelimiter=" … "'
)


class SearchMode(StrEnum):
    HYBRID = "hybrid"
    KEYWORD = "keyword"
    SEMANTIC = "semantic"


@dataclass(slots=True)
class SearchHit:
    section_id: uuid.UUID
    score: float
    matched_by: list[str] = field(default_factory=list)
    document_id: uuid.UUID | None = None
    document_title: str = ""
    section_index: int = 0
    heading: str | None = None
    page_numbers: list[int] = field(default_factory=list)
    snippet: str = ""


@dataclass(slots=True)
class SearchResult:
    hits: list[SearchHit]
    total: int
    degraded: bool = False


def reciprocal_rank_fusion(
    rankings: dict[str, list[uuid.UUID]], *, k: int = RRF_K
) -> list[tuple[uuid.UUID, float, list[str]]]:
    scores: dict[uuid.UUID, float] = {}
    sources: dict[uuid.UUID, list[str]] = {}
    for name, ids in rankings.items():
        for rank, item in enumerate(ids, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            sources.setdefault(item, []).append(name)
    ordered = sorted(scores, key=lambda i: (-scores[i], str(i)))
    return [(i, scores[i], sources[i]) for i in ordered]


def any_term_tsquery(query: str) -> ColumnElement[Any]:
    """``websearch_to_tsquery`` with its top-level ANDs turned into ORs.

    Retrieval wants recall: a section matching some of the words is a candidate, and
    ``ts_rank_cd`` ranks sections matching more of them first. Quoted phrases keep
    their adjacency operator; queries using ``-exclusion`` keep strict AND semantics.
    """
    strict = func.websearch_to_tsquery(ENGLISH, query)
    as_text = cast(strict, Text)
    return case(
        (func.strpos(as_text, "!") > 0, strict),
        else_=cast(func.replace(as_text, " & ", " | "), TSQUERY),
    )


def _section_tsvector() -> ColumnElement[Any]:
    # Must match the index expression `to_tsvector('english', content)` to use it.
    return func.to_tsvector(ENGLISH, DocumentSection.content)


class HybridRetriever:
    def __init__(
        self,
        session: AsyncSession,
        *,
        vectors: SectionVectorStore | None,
        llm: LLMClient | None,
        recorder: AIUsageRecorder | None,
    ) -> None:
        self.session = session
        self.vectors = vectors
        self.llm = llm
        self.recorder = recorder

    def _scope(
        self,
        user_id: uuid.UUID,
        subject_id: uuid.UUID | None,
        document_id: uuid.UUID | None,
        concept_id: uuid.UUID | None,
    ) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = [Document.user_id == user_id]
        if subject_id:
            filters.append(Document.subject_id == subject_id)
        if document_id:
            filters.append(DocumentSection.document_id == document_id)
        if concept_id:
            filters.append(
                exists().where(
                    DocumentSectionConcept.document_section_id == DocumentSection.id,
                    DocumentSectionConcept.concept_id == concept_id,
                )
            )
        return filters

    async def keyword(
        self, user_id: uuid.UUID, query: str, *, limit: int, **scope: uuid.UUID | None
    ) -> list[uuid.UUID]:
        tsquery = any_term_tsquery(query)
        vector = _section_tsvector()
        rows = await self.session.scalars(
            select(DocumentSection.id)
            .join(Document, Document.id == DocumentSection.document_id)
            .where(vector.op("@@")(tsquery), *self._scope(user_id, **scope))
            .order_by(func.ts_rank_cd(vector, tsquery).desc(), DocumentSection.id)
            .limit(limit)
        )
        return list(rows.all())

    async def semantic(
        self, user_id: uuid.UUID, query: str, *, limit: int, **scope: uuid.UUID | None
    ) -> list[uuid.UUID]:
        if self.vectors is None or self.llm is None or self.recorder is None:
            raise VectorStoreError("Vector search is not configured")
        trace_id = await self.recorder.start_trace(user_id, "content_search")
        ctx = AICallContext(user_id=user_id, trace_id=trace_id, purpose="search_query_embedding")
        try:
            [vector] = await self.llm.embed([query], ctx)
            hits = await self.vectors.search(user_id, vector, limit=limit, **scope)
        except Exception:
            await self.recorder.finish_trace(trace_id, "failed")
            raise
        await self.recorder.finish_trace(trace_id, "completed")
        # Vectors may outlive a deleted section briefly; keep only rows the user still owns.
        ids = [h.section_id for h in hits]
        if not ids:
            return []
        owned = set(
            (
                await self.session.scalars(
                    select(DocumentSection.id)
                    .join(Document, Document.id == DocumentSection.document_id)
                    .where(DocumentSection.id.in_(ids), *self._scope(user_id, **scope))
                )
            ).all()
        )
        return [i for i in ids if i in owned]

    async def search(
        self,
        user_id: uuid.UUID,
        query: str,
        *,
        mode: SearchMode = SearchMode.HYBRID,
        offset: int = 0,
        limit: int = 20,
        candidates: int = 100,
        subject_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
        concept_id: uuid.UUID | None = None,
    ) -> SearchResult:
        scope: dict[str, uuid.UUID | None] = {
            "subject_id": subject_id,
            "document_id": document_id,
            "concept_id": concept_id,
        }
        rankings: dict[str, list[uuid.UUID]] = {}
        degraded = False
        if mode in (SearchMode.HYBRID, SearchMode.KEYWORD):
            rankings["keyword"] = await self.keyword(user_id, query, limit=candidates, **scope)
        if mode in (SearchMode.HYBRID, SearchMode.SEMANTIC):
            try:
                rankings["semantic"] = await self.semantic(
                    user_id, query, limit=candidates, **scope
                )
            except AIBudgetExceededError:
                if mode is SearchMode.SEMANTIC:
                    raise
                degraded = True
            except (LLMError, VectorStoreError) as exc:
                if mode is SearchMode.SEMANTIC:
                    raise
                logger.warning("semantic search unavailable", extra={"error": type(exc).__name__})
                degraded = True

        fused = reciprocal_rank_fusion(rankings)
        page = fused[offset : offset + limit]
        hits = [SearchHit(section_id=i, score=round(s, 6), matched_by=src) for i, s, src in page]
        await self._hydrate(hits, query, "keyword" in rankings)
        return SearchResult(hits=hits, total=len(fused), degraded=degraded)

    async def _hydrate(self, hits: list[SearchHit], query: str, with_headline: bool) -> None:
        if not hits:
            return
        columns: list[Any] = [
            DocumentSection.id,
            DocumentSection.document_id,
            Document.title,
            DocumentSection.section_index,
            DocumentSection.heading,
            DocumentSection.page_numbers,
            func.left(DocumentSection.content, SNIPPET_CHARS),
        ]
        if with_headline:
            columns.append(
                func.ts_headline(
                    ENGLISH,
                    DocumentSection.content,
                    any_term_tsquery(query),
                    HEADLINE_OPTIONS,
                )
            )
        rows = await self.session.execute(
            select(*columns)
            .join(Document, Document.id == DocumentSection.document_id)
            .where(DocumentSection.id.in_([h.section_id for h in hits]))
        )
        by_id = {row[0]: row for row in rows}
        for hit in hits:
            row = by_id.get(hit.section_id)
            if row is None:
                continue
            hit.document_id, hit.document_title, hit.section_index = row[1], row[2], row[3]
            hit.heading, hit.page_numbers = row[4], list(row[5] or [])
            headline = row[7] if with_headline and "keyword" in hit.matched_by else None
            hit.snippet = " ".join((headline or row[6] or "").split())
        hits[:] = [h for h in hits if h.document_id is not None]
