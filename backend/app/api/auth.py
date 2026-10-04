"""`POST /auth/callback` — sync the Supabase identity into PostgreSQL (API_CONTRACT §3.2).

Idempotent: 201 when the local user is created, 200 when it already existed.
Identity (auth_id, email) always comes from the verified JWT, never the body.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import DbSession, TokenClaims, provision_user, rate_limit_auth
from app.db.repositories.user import ProfileRepository
from app.domain.common import Envelope, envelope
from app.domain.user import AuthCallbackRequest, AuthCallbackResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/callback",
    response_model=Envelope[AuthCallbackResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
    responses={200: {"description": "User already existed"}},
)
async def auth_callback(
    body: AuthCallbackRequest,
    request: Request,
    response: Response,
    claims: TokenClaims,
    session: DbSession,
) -> Envelope[AuthCallbackResponse]:
    user, created = await provision_user(claims, session)
    profile = await ProfileRepository(session).ensure_for_user(user.id)
    if body.display_name and not user.display_name:
        user.display_name = body.display_name
    await session.commit()

    if not created:
        response.status_code = status.HTTP_200_OK
    return envelope(
        request,
        AuthCallbackResponse(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            onboarding_state=profile.onboarding_state,
        ),
    )
