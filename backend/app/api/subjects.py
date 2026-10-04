"""Curriculum endpoints — Subject → Course → Chapter → Section (API_CONTRACT §3.4).

Collections are created and listed under their parent
(``/subjects/{id}/courses``, ``/courses/{id}/chapters``, ``/chapters/{id}/sections``);
individual items are addressed directly (``/courses/{id}`` etc.). Every lookup is
scoped to the current user; anything owned by someone else is a 404.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession, PageParams
from app.core.exceptions import ConflictError, NotFoundError
from app.db.base import Base
from app.db.models import Chapter, Course, Section
from app.db.repositories.base import ScopedRepository
from app.db.repositories.curriculum import (
    ChapterRepository,
    CourseRepository,
    SectionRepository,
    SubjectRepository,
    SubjectWithStats,
)
from app.domain.common import Envelope, PaginatedEnvelope, envelope, paginated
from app.domain.curriculum import (
    ChapterCreate,
    ChapterRead,
    ChapterUpdate,
    CourseCreate,
    CourseRead,
    CourseUpdate,
    SectionCreate,
    SectionRead,
    SectionUpdate,
    SubjectCreate,
    SubjectRead,
    SubjectUpdate,
)

router = APIRouter(tags=["curriculum"])

_SUBJECT_NAME_CONSTRAINT = "subjects_user_id_name_key"


async def _get_or_404[M: Base](
    repo: ScopedRepository[M], id_: uuid.UUID, user_id: uuid.UUID, label: str
) -> M:
    obj = await repo.get_by_id(id_, user_id)
    if obj is None:
        raise NotFoundError(f"{label} not found")
    return obj


def _subject_read(s: SubjectWithStats) -> SubjectRead:
    subject = s.subject
    return SubjectRead(
        id=subject.id,
        name=subject.name,
        description=subject.description,
        icon=subject.icon,
        course_count=s.course_count,
        concept_count=s.concept_count,
        avg_mastery=round(s.avg_mastery, 4),
        created_at=subject.created_at,
        updated_at=subject.updated_at,
    )


@asynccontextmanager
async def _unique_subject_name(session: AsyncSession) -> AsyncIterator[None]:
    """Translate the UNIQUE(user_id, name) violation into a 409 CONFLICT."""
    try:
        yield
    except IntegrityError as exc:
        await session.rollback()
        if _SUBJECT_NAME_CONSTRAINT in str(exc.orig):
            raise ConflictError(
                "A subject with this name already exists", details={"field": "name"}
            ) from exc
        raise


# --- Subjects --------------------------------------------------------------------------


@router.get("/subjects", response_model=PaginatedEnvelope[SubjectRead])
async def list_subjects(
    request: Request, user: CurrentUser, session: DbSession, page: PageParams
) -> PaginatedEnvelope[SubjectRead]:
    result = await SubjectRepository(session).paginate_with_stats(user.id, page)
    return paginated(
        request,
        [_subject_read(s) for s in result.items],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
    )


@router.post("/subjects", response_model=Envelope[SubjectRead], status_code=status.HTTP_201_CREATED)
async def create_subject(
    body: SubjectCreate, request: Request, user: CurrentUser, session: DbSession
) -> Envelope[SubjectRead]:
    async with _unique_subject_name(session):
        subject = await SubjectRepository(session).create(user.id, **body.model_dump())
        await session.commit()
    return envelope(request, _subject_read(SubjectWithStats(subject, 0, 0, 0.0)))


@router.get("/subjects/{subject_id}", response_model=Envelope[SubjectRead])
async def get_subject(
    subject_id: uuid.UUID, request: Request, user: CurrentUser, session: DbSession
) -> Envelope[SubjectRead]:
    found = await SubjectRepository(session).get_with_stats(subject_id, user.id)
    if found is None:
        raise NotFoundError("Subject not found")
    return envelope(request, _subject_read(found))


@router.patch("/subjects/{subject_id}", response_model=Envelope[SubjectRead])
async def update_subject(
    subject_id: uuid.UUID,
    body: SubjectUpdate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> Envelope[SubjectRead]:
    repo = SubjectRepository(session)
    subject = await _get_or_404(repo, subject_id, user.id, "Subject")
    async with _unique_subject_name(session):
        await repo.update(subject, body.model_dump(exclude_unset=True))
        await session.commit()
    found = await repo.get_with_stats(subject_id, user.id)
    if found is None:  # pragma: no cover - deleted concurrently
        raise NotFoundError("Subject not found")
    return envelope(request, _subject_read(found))


@router.delete("/subjects/{subject_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subject(subject_id: uuid.UUID, user: CurrentUser, session: DbSession) -> Response:
    repo = SubjectRepository(session)
    await repo.delete(await _get_or_404(repo, subject_id, user.id, "Subject"))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Courses ---------------------------------------------------------------------------


@router.get("/subjects/{subject_id}/courses", response_model=PaginatedEnvelope[CourseRead])
async def list_courses(
    subject_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    page: PageParams,
) -> PaginatedEnvelope[CourseRead]:
    await _get_or_404(SubjectRepository(session), subject_id, user.id, "Subject")
    result = await CourseRepository(session).paginate(
        user.id, page, Course.subject_id == subject_id
    )
    return paginated(
        request,
        [CourseRead.model_validate(c) for c in result.items],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
    )


@router.post(
    "/subjects/{subject_id}/courses",
    response_model=Envelope[CourseRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_course(
    subject_id: uuid.UUID,
    body: CourseCreate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> Envelope[CourseRead]:
    await _get_or_404(SubjectRepository(session), subject_id, user.id, "Subject")
    course = await CourseRepository(session).add(Course(subject_id=subject_id, **body.model_dump()))
    await session.commit()
    return envelope(request, CourseRead.model_validate(course))


@router.get("/courses/{course_id}", response_model=Envelope[CourseRead])
async def get_course(
    course_id: uuid.UUID, request: Request, user: CurrentUser, session: DbSession
) -> Envelope[CourseRead]:
    course = await _get_or_404(CourseRepository(session), course_id, user.id, "Course")
    return envelope(request, CourseRead.model_validate(course))


@router.patch("/courses/{course_id}", response_model=Envelope[CourseRead])
async def update_course(
    course_id: uuid.UUID,
    body: CourseUpdate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> Envelope[CourseRead]:
    repo = CourseRepository(session)
    course = await _get_or_404(repo, course_id, user.id, "Course")
    await repo.update(course, body.model_dump(exclude_unset=True))
    await session.commit()
    return envelope(request, CourseRead.model_validate(course))


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(course_id: uuid.UUID, user: CurrentUser, session: DbSession) -> Response:
    repo = CourseRepository(session)
    await repo.delete(await _get_or_404(repo, course_id, user.id, "Course"))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Chapters --------------------------------------------------------------------------


@router.get("/courses/{course_id}/chapters", response_model=PaginatedEnvelope[ChapterRead])
async def list_chapters(
    course_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    page: PageParams,
) -> PaginatedEnvelope[ChapterRead]:
    await _get_or_404(CourseRepository(session), course_id, user.id, "Course")
    result = await ChapterRepository(session).paginate(
        user.id, page, Chapter.course_id == course_id
    )
    return paginated(
        request,
        [ChapterRead.model_validate(c) for c in result.items],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
    )


@router.post(
    "/courses/{course_id}/chapters",
    response_model=Envelope[ChapterRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_chapter(
    course_id: uuid.UUID,
    body: ChapterCreate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> Envelope[ChapterRead]:
    await _get_or_404(CourseRepository(session), course_id, user.id, "Course")
    chapter = await ChapterRepository(session).add(
        Chapter(course_id=course_id, **body.model_dump())
    )
    await session.commit()
    return envelope(request, ChapterRead.model_validate(chapter))


@router.get("/chapters/{chapter_id}", response_model=Envelope[ChapterRead])
async def get_chapter(
    chapter_id: uuid.UUID, request: Request, user: CurrentUser, session: DbSession
) -> Envelope[ChapterRead]:
    chapter = await _get_or_404(ChapterRepository(session), chapter_id, user.id, "Chapter")
    return envelope(request, ChapterRead.model_validate(chapter))


@router.patch("/chapters/{chapter_id}", response_model=Envelope[ChapterRead])
async def update_chapter(
    chapter_id: uuid.UUID,
    body: ChapterUpdate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> Envelope[ChapterRead]:
    repo = ChapterRepository(session)
    chapter = await _get_or_404(repo, chapter_id, user.id, "Chapter")
    await repo.update(chapter, body.model_dump(exclude_unset=True))
    await session.commit()
    return envelope(request, ChapterRead.model_validate(chapter))


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chapter(chapter_id: uuid.UUID, user: CurrentUser, session: DbSession) -> Response:
    repo = ChapterRepository(session)
    await repo.delete(await _get_or_404(repo, chapter_id, user.id, "Chapter"))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Sections --------------------------------------------------------------------------


@router.get("/chapters/{chapter_id}/sections", response_model=PaginatedEnvelope[SectionRead])
async def list_sections(
    chapter_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    page: PageParams,
) -> PaginatedEnvelope[SectionRead]:
    await _get_or_404(ChapterRepository(session), chapter_id, user.id, "Chapter")
    result = await SectionRepository(session).paginate(
        user.id, page, Section.chapter_id == chapter_id
    )
    return paginated(
        request,
        [SectionRead.model_validate(s) for s in result.items],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
    )


@router.post(
    "/chapters/{chapter_id}/sections",
    response_model=Envelope[SectionRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_section(
    chapter_id: uuid.UUID,
    body: SectionCreate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> Envelope[SectionRead]:
    await _get_or_404(ChapterRepository(session), chapter_id, user.id, "Chapter")
    section = await SectionRepository(session).add(
        Section(chapter_id=chapter_id, **body.model_dump())
    )
    await session.commit()
    return envelope(request, SectionRead.model_validate(section))


@router.get("/sections/{section_id}", response_model=Envelope[SectionRead])
async def get_section(
    section_id: uuid.UUID, request: Request, user: CurrentUser, session: DbSession
) -> Envelope[SectionRead]:
    section = await _get_or_404(SectionRepository(session), section_id, user.id, "Section")
    return envelope(request, SectionRead.model_validate(section))


@router.patch("/sections/{section_id}", response_model=Envelope[SectionRead])
async def update_section(
    section_id: uuid.UUID,
    body: SectionUpdate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> Envelope[SectionRead]:
    repo = SectionRepository(session)
    section = await _get_or_404(repo, section_id, user.id, "Section")
    await repo.update(section, body.model_dump(exclude_unset=True))
    await session.commit()
    return envelope(request, SectionRead.model_validate(section))


@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(section_id: uuid.UUID, user: CurrentUser, session: DbSession) -> Response:
    repo = SectionRepository(session)
    await repo.delete(await _get_or_404(repo, section_id, user.id, "Section"))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
