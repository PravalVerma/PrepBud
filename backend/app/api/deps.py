"""Shared FastAPI dependencies: DB session, authentication, pagination, rate limits."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, Query, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    RateLimitedError,
)
from app.core.logging import get_logger, user_id_ctx
from app.core.middleware import SlidingWindowRateLimiter
from app.core.security import JWTVerifier
from app.db.models import User
from app.db.repositories.base import PageRequest
from app.db.repositories.user import AuthIdentity, UserRepository
from app.domain.common import DEFAULT_PER_PAGE, MAX_PER_PAGE

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
    request: Request, response: Response, *, subject: str, scope: str, limit: int
) -> None:
    settings: Settings = request.app.state.settings
    if not settings.rate_limit_enabled:
        return
    limiter = SlidingWindowRateLimiter(request.app.state.redis)
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
