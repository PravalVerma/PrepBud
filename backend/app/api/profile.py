"""`GET/PATCH /profile` — the current user's StudentProfile (API_CONTRACT §3.3)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, DbSession
from app.db.models import StudentProfile, User
from app.db.repositories.user import ProfileRepository
from app.domain.common import Envelope, envelope
from app.domain.user import ProfileRead, ProfileUpdate

router = APIRouter(prefix="/profile", tags=["profile"])

_USER_FIELDS = frozenset({"display_name"})


def _to_read(user: User, profile: StudentProfile) -> ProfileRead:
    return ProfileRead(
        id=profile.id,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        grade_level=profile.grade_level,
        difficulty_band=profile.difficulty_band,
        language=profile.language,
        timezone=profile.timezone,
        preferences=profile.preferences or {},
        cumulative_stats=profile.cumulative_stats or {},
        onboarding_state=profile.onboarding_state,
    )


@router.get("", response_model=Envelope[ProfileRead])
async def get_profile(
    request: Request, user: CurrentUser, session: DbSession
) -> Envelope[ProfileRead]:
    profile = await ProfileRepository(session).ensure_for_user(user.id)
    await session.commit()
    return envelope(request, _to_read(user, profile))


@router.patch("", response_model=Envelope[ProfileRead])
async def update_profile(
    body: ProfileUpdate, request: Request, user: CurrentUser, session: DbSession
) -> Envelope[ProfileRead]:
    profile = await ProfileRepository(session).ensure_for_user(user.id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(user if field in _USER_FIELDS else profile, field, value)
    await session.commit()
    await session.refresh(profile)
    return envelope(request, _to_read(user, profile))
