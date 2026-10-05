"""Shared FastAPI dependencies: DB session, authentication, pagination, rate limits."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, Query, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.cost_tracker import AIUsageRecorder
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import get_prompt_manager
from app.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    RateLimitedError,
    ServiceUnavailableError,
)
from app.core.logging import get_logger, user_id_ctx
from app.core.middleware import SlidingWindowRateLimiter
from app.core.security import JWTVerifier
from app.db.models import User
from app.db.repositories.base import PageRequest
from app.db.repositories.user import AuthIdentity, UserRepository
from app.domain.common import DEFAULT_PER_PAGE, MAX_PER_PAGE
from app.integrations.qdrant import SectionVectorStore
from app.integrations.s3 import ObjectStorage
from app.services.content.document_processor import TaskQueue
from app.services.learning_engine.session_manager import SessionManager

logger = get_logger(__name__)

_bearer = HTTPBearer(auto_error=False, description="Supabase access token")


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]


# --- Authentication -----------------------------------------------------------------


async def get_token_claims(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError()
    verifier: JWTVerifier = request.app.state.jwt_verifier
    return await verifier.verify(credentials.credentials)


TokenClaims = Annotated[dict[str, Any], Depends(get_token_claims)]


def identity_from_claims(claims: dict[str, Any]) -> AuthIdentity:
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise AuthenticationError("Token is missing the email claim")
    metadata = claims.get("user_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    display_name = metadata.get("full_name") or metadata.get("name")
    avatar_url = metadata.get("avatar_url")
    return AuthIdentity(
        auth_id=claims["sub"],
        email=email.lower(),
        display_name=display_name if isinstance(display_name, str) else None,
        avatar_url=avatar_url if isinstance(avatar_url, str) else None,
    )


async def provision_user(claims: dict[str, Any], session: AsyncSession) -> tuple[User, bool]:
    """Resolve token claims to a local user, creating User + StudentProfile on first sight."""
    user, created = await UserRepository(session).get_or_create(identity_from_claims(claims))
    if user is None:
        raise ConflictError("This email address is already linked to another account")
    if user.is_active is False:
        raise ForbiddenError("This account is disabled")
    user_id_ctx.set(str(user.id))
    if created:
        logger.info("user provisioned", extra={"user_id": str(user.id)})
    return user, created


async def get_current_user(claims: TokenClaims, session: DbSession) -> User:
    user, _ = await provision_user(claims, session)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# --- Pagination ------------------------------------------------------------------------


def get_page_request(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE, description="Items per page"),
) -> PageRequest:
    return PageRequest(page=page, per_page=per_page)


PageParams = Annotated[PageRequest, Depends(get_page_request)]


# --- Rate limiting (SECURITY_MODEL §5.1) -------------------------------------------------


async def _enforce_rate_limit(
    request: Request,
    response: Response,
    *,
    subject: str,
    scope: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    settings: Settings = request.app.state.settings
    if not settings.rate_limit_enabled:
        return
    limiter = SlidingWindowRateLimiter(request.app.state.redis, window_seconds)
    try:
        result = await limiter.hit(subject, scope, limit)
    except Exception as exc:
        # Availability over strictness: a Redis outage must not take the API down.
        logger.warning("rate limiter unavailable", extra={"error": type(exc).__name__})
        return
    if not result.allowed:
        raise RateLimitedError(
            "Rate limit exceeded. Try again later.",
            headers={**result.headers(), "Retry-After": str(limiter.window)},
        )
    response.headers.update(result.headers())


async def rate_limit_api(request: Request, response: Response, user: CurrentUser) -> None:
    settings: Settings = request.app.state.settings
    await _enforce_rate_limit(
        request, response, subject=str(user.id), scope="api", limit=settings.rate_limit_per_minute
    )


async def rate_limit_auth(request: Request, response: Response, claims: TokenClaims) -> None:
    settings: Settings = request.app.state.settings
    await _enforce_rate_limit(
        request,
        response,
        subject=str(claims["sub"]),
        scope="auth",
        limit=settings.rate_limit_auth_per_minute,
    )


async def rate_limit_uploads(request: Request, response: Response, user: CurrentUser) -> None:
    """Document uploads: N per user per hour (SECURITY_MODEL §5.1)."""
    settings: Settings = request.app.state.settings
    await _enforce_rate_limit(
        request,
        response,
        subject=str(user.id),
        scope="upload",
        limit=settings.rate_limit_uploads_per_hour,
        window_seconds=3600,
    )


# --- Phase 3 services (created at startup; ``None`` when not configured) ---------------------


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage | None = request.app.state.storage
    if storage is None:
        raise ServiceUnavailableError("File storage is not configured")
    return storage


def get_optional_storage(request: Request) -> ObjectStorage | None:
    storage: ObjectStorage | None = request.app.state.storage
    return storage


def get_vector_store(request: Request) -> SectionVectorStore | None:
    vectors: SectionVectorStore | None = request.app.state.vector_store
    return vectors


def get_task_queue(request: Request) -> TaskQueue:
    queue: TaskQueue = request.app.state.task_queue
    return queue


def get_llm(request: Request) -> LLMClient:
    llm: LLMClient = request.app.state.llm
    return llm


def get_recorder(request: Request) -> AIUsageRecorder:
    recorder: AIUsageRecorder = request.app.state.ai_recorder
    return recorder


Storage = Annotated[ObjectStorage, Depends(get_storage)]
OptionalStorage = Annotated[ObjectStorage | None, Depends(get_optional_storage)]
VectorStore = Annotated[SectionVectorStore | None, Depends(get_vector_store)]
Queue = Annotated[TaskQueue, Depends(get_task_queue)]
LLM = Annotated[LLMClient, Depends(get_llm)]
Recorder = Annotated[AIUsageRecorder, Depends(get_recorder)]


async def rate_limit_sessions(request: Request, response: Response, user: CurrentUser) -> None:
    """Session starts: N per user per hour (SECURITY_MODEL 5.1)."""
    settings: Settings = request.app.state.settings
    await _enforce_rate_limit(
        request,
        response,
        subject=str(user.id),
        scope="session_start",
        limit=settings.rate_limit_sessions_per_hour,
        window_seconds=3600,
    )


def get_session_manager(request: Request) -> SessionManager:
    """Built per request from app state so tests can swap the LLM / stores after startup."""
    state = request.app.state
    return SessionManager(
        settings=state.settings,
        sessionmaker=state.sessionmaker,
        llm=state.llm,
        recorder=state.ai_recorder,
        prompts=get_prompt_manager(),
        redis=state.redis,
        vectors=state.vector_store,
    )


Sessions = Annotated[SessionManager, Depends(get_session_manager)]
