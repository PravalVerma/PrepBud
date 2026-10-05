"""Concept endpoints (API_CONTRACT §3.6) and hybrid content search (added in Phase 3).

Every lookup is user-scoped; another user's concept is a 404.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response

from app.api.deps import LLM, CurrentUser, DbSession, PageParams, Recorder, VectorStore
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.db.repositories.concept import ConceptRepository, SortField
from app.domain.common import Envelope, PaginatedEnvelope, envelope, paginated
from app.domain.concept import (
    ConceptDetail,
    ConceptDocumentLink,
    ConceptGraph,
    ConceptLink,
    ConceptListItem,
    GraphEdgeRead,
    GraphNodeRead,
    MasteryDetail,
    MasteryHistoryPoint,
    MasterySummary,
    MisconceptionLink,
    RelatedConcept,
    SearchHitRead,
)
from app.services.content.retriever import HybridRetriever, SearchMode

router = APIRouter(tags=["concepts"])


@router.get("/concepts", response_model=PaginatedEnvelope[ConceptListItem], summary="List concepts")
async def list_concepts(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    page: PageParams,
    subject_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    mastery_below: float | None = Query(None, ge=0.0, le=1.0),
    search: str | None = Query(None, min_length=1, max_length=200),
    sort: SortField = "name",
    order: Literal["asc", "desc"] = "asc",
) -> PaginatedEnvelope[ConceptListItem]:
    result = await ConceptRepository(session).list_with_stats(
        user.id,
        page,
        subject_id=subject_id,
        chapter_id=chapter_id,
        document_id=document_id,
        mastery_below=mastery_below,
        search=search.strip() if search else None,
        sort=sort,
        order=order,
    )
    items = [
        ConceptListItem(
            id=row.concept.id,
            name=row.concept.name,
            description=row.concept.description,
            difficulty_estimate=row.concept.difficulty_estimate or 0.5,
            subject_id=row.concept.subject_id,
            chapter_id=row.concept.chapter_id,
            mastery=MasterySummary.build(
                row.mastery.mastery_level if row.mastery else None,
                row.mastery.last_assessed_at if row.mastery else None,
                row.mastery.next_review_at if row.mastery else None,
            ),
            prerequisite_count=row.prerequisite_count,
            document_count=row.document_count,
        )
        for row in result.items
    ]
    return paginated(request, items, total=result.total, page=result.page, per_page=result.per_page)


def _history(raw: Any) -> list[MasteryHistoryPoint]:
    points = []
    for item in raw if isinstance(raw, list) else []:
        try:
            points.append(MasteryHistoryPoint.model_validate(item))
        except ValueError:
            continue
    return points


@router.get("/concepts/{concept_id}", response_model=Envelope[ConceptDetail])
async def get_concept(
    concept_id: uuid.UUID, request: Request, user: CurrentUser, session: DbSession
) -> Envelope[ConceptDetail]:
    repo = ConceptRepository(session)
    concept = await repo.get_by_id(concept_id, user.id)
    if concept is None:
        raise NotFoundError("Concept not found")
    scm = await repo.mastery(concept.id, user.id)
    summary = MasterySummary.build(
        scm.mastery_level if scm else None,
        scm.last_assessed_at if scm else None,
        scm.next_review_at if scm else None,
    )
    mastery = MasteryDetail(
        **summary.model_dump(),
        confidence=(scm.confidence or 0.0) if scm else 0.0,
        attempt_count=(scm.attempt_count or 0) if scm else 0,
        correct_count=(scm.correct_count or 0) if scm else 0,
        streak=(scm.streak or 0) if scm else 0,
        history=_history(scm.history) if scm else [],
    )
    detail = ConceptDetail(
        id=concept.id,
        name=concept.name,
        description=concept.description,
        difficulty_estimate=concept.difficulty_estimate or 0.5,
        subject_id=concept.subject_id,
        chapter_id=concept.chapter_id,
        section_id=concept.section_id,
        mastery=mastery,
        prerequisites=[
            ConceptLink(id=c.id, name=c.name, mastery_level=c.mastery_level)
            for c in await repo.prerequisites(concept.id, user.id)
        ],
        dependents=[
            ConceptLink(id=c.id, name=c.name, mastery_level=c.mastery_level)
            for c in await repo.dependents(concept.id, user.id)
        ],
        related_concepts=[
            RelatedConcept(id=c.id, name=c.name, relationship=c.relationship or "related")
            for c in await repo.related(concept.id, user.id)
        ],
        misconceptions=[
            MisconceptionLink(id=m.id, name=m.name, status=m.status)
            for m in await repo.misconceptions(concept.id, user.id)
        ],
        documents=[
            ConceptDocumentLink(id=d.id, title=d.title, section_count=d.section_count)
            for d in await repo.documents(concept.id, user.id)
        ],
        created_at=concept.created_at,
        metadata={k: v for k, v in (concept.metadata_ or {}).items() if k in ("origin", "aliases")},
    )
    return envelope(request, detail)


@router.get("/concepts/{concept_id}/graph", response_model=Envelope[ConceptGraph])
async def get_concept_graph(
    concept_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    depth: int = Query(1, ge=1, le=3, description="Hops to include around the concept"),
) -> Envelope[ConceptGraph]:
    repo = ConceptRepository(session)
    if await repo.get_by_id(concept_id, user.id) is None:
        raise NotFoundError("Concept not found")
    nodes, edges = await repo.neighbourhood(concept_id, user.id, depth=depth)
    return envelope(
        request,
        ConceptGraph(
            nodes=[
                GraphNodeRead(
                    id=n.id, name=n.name, mastery=round(n.mastery, 4), is_target=n.id == concept_id
                )
                for n in nodes
            ],
            edges=[GraphEdgeRead(source=e.source, target=e.target, type=e.type) for e in edges],
        ),
    )


# --- Search ----------------------------------------------------------------------------------

search_router = APIRouter(tags=["search"])


@search_router.get(
    "/search",
    response_model=PaginatedEnvelope[SearchHitRead],
    summary="Hybrid (semantic + keyword) search over the user's document sections",
)
async def search_sections(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: DbSession,
    page: PageParams,
    vectors: VectorStore,
    llm: LLM,
    recorder: Recorder,
    q: str = Query(..., min_length=1, max_length=500, description="Search text"),
    mode: SearchMode = SearchMode.HYBRID,
    subject_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    concept_id: uuid.UUID | None = None,
) -> PaginatedEnvelope[SearchHitRead]:
    query = " ".join(q.split())
    if not query:
        return paginated(request, [], total=0, page=page.page, per_page=page.per_page)
    retriever = HybridRetriever(session, vectors=vectors, llm=llm, recorder=recorder)
    try:
        result = await retriever.search(
            user.id,
            query,
            mode=mode,
            offset=page.offset,
            limit=page.per_page,
            subject_id=subject_id,
            document_id=document_id,
            concept_id=concept_id,
        )
    except Exception as exc:
        if mode is SearchMode.SEMANTIC:
            raise ServiceUnavailableError("Semantic search is unavailable right now") from exc
        raise
    response.headers["X-Search-Mode"] = "keyword" if result.degraded else mode.value
    if result.degraded:
        response.headers["X-Search-Degraded"] = "true"
    hits = [
        SearchHitRead(
            section_id=h.section_id,
            document_id=h.document_id,
            document_title=h.document_title,
            section_index=h.section_index,
            heading=h.heading,
            page_numbers=h.page_numbers,
            snippet=h.snippet,
            score=h.score,
            matched_by=h.matched_by,
        )
        for h in result.hits
        if h.document_id is not None
    ]
    return paginated(request, hits, total=result.total, page=page.page, per_page=page.per_page)
