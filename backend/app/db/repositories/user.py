"""User + StudentProfile data access, including first-login provisioning."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StudentProfile, User


@dataclass(frozen=True, slots=True)
class AuthIdentity:
    """Identity attributes taken from a *verified* Supabase JWT."""

    auth_id: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_auth_id(self, auth_id: str) -> User | None:
        return await self.session.scalar(select(User).where(User.auth_id == auth_id))

    async def get_or_create(self, identity: AuthIdentity) -> tuple[User | None, bool]:
        """Return the user for ``identity``, provisioning User + StudentProfile on first sight.

        Concurrency-safe: simultaneous first requests race on ``INSERT ... ON CONFLICT
        DO NOTHING`` and all end up reading the single winning row. Returns
        ``(None, False)`` when the email is already bound to a different auth
        identity (the caller turns this into a 409).
        """
        user = await self.get_by_auth_id(identity.auth_id)
        if user is not None:
            return user, False

        inserted_id = await self.session.scalar(
            insert(User)
            .values(
                auth_id=identity.auth_id,
                email=identity.email,
                display_name=identity.display_name,
                avatar_url=identity.avatar_url,
            )
            .on_conflict_do_nothing()
            .returning(User.id)
        )
        if inserted_id is not None:
            await self.session.execute(
                insert(StudentProfile)
                .values(user_id=inserted_id)
                .on_conflict_do_nothing(index_elements=[StudentProfile.user_id])
            )
        await self.session.commit()

        user = await self.get_by_auth_id(identity.auth_id)
        return user, inserted_id is not None


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: uuid.UUID) -> StudentProfile | None:
        return await self.session.scalar(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )

    async def ensure_for_user(self, user_id: uuid.UUID) -> StudentProfile:
        """Fetch the profile, recreating it if it is somehow missing (invariant: 1:1)."""
        profile = await self.get_for_user(user_id)
        if profile is None:
            await self.session.execute(
                insert(StudentProfile)
                .values(user_id=user_id)
                .on_conflict_do_nothing(index_elements=[StudentProfile.user_id])
            )
            await self.session.flush()
            profile = await self.get_for_user(user_id)
        if profile is None:  # pragma: no cover - guarded by the insert above
            raise RuntimeError(f"StudentProfile missing for user {user_id}")
        return profile
