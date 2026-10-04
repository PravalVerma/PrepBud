"""User and StudentProfile schemas (API_CONTRACT §3.2–3.3)."""

from __future__ import annotations

import uuid
from functools import cache
from typing import Any, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.domain.common import ORMModel
from app.domain.types import ShortText

DifficultyBand = Literal["novice", "beginner", "intermediate", "advanced"]
OnboardingState = Literal["new", "profile_set", "first_upload", "first_session", "complete"]

_LANGUAGE_PATTERN = r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$"
_MAX_JSON_KEYS = 50


# --- Auth ----------------------------------------------------------------------


class AuthCallbackRequest(BaseModel):
    """Body of ``POST /auth/callback``.

    ``auth_id`` and ``email`` are accepted for contract compatibility but the
    authoritative values always come from the verified JWT.
    """

    model_config = ConfigDict(extra="ignore")

    auth_id: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = None
    display_name: ShortText | None = None


class AuthCallbackResponse(ORMModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    onboarding_state: str | None


# --- Profile ---------------------------------------------------------------------


class ProfileRead(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    avatar_url: str | None
    grade_level: str | None
    difficulty_band: str | None
    language: str | None
    timezone: str | None
    preferences: dict[str, Any]
    cumulative_stats: dict[str, Any]
    onboarding_state: str | None


class ProfileUpdate(BaseModel):
    """``PATCH /profile`` — every field optional; only provided fields change."""

    model_config = ConfigDict(extra="forbid")

    display_name: ShortText | None = None
    grade_level: ShortText | None = None
    difficulty_band: DifficultyBand | None = None
    language: str | None = Field(default=None, pattern=_LANGUAGE_PATTERN, max_length=35)
    timezone: str | None = Field(default=None, max_length=64)
    preferences: dict[str, Any] | None = None
    onboarding_state: OnboardingState | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str | None) -> str | None:
        if value is not None and value not in _timezones():
            raise ValueError("must be a valid IANA timezone, e.g. 'Asia/Kolkata'")
        return value

    @field_validator("preferences")
    @classmethod
    def _bounded_preferences(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(value) > _MAX_JSON_KEYS:
            raise ValueError(f"must not have more than {_MAX_JSON_KEYS} keys")
        return value

    @model_validator(mode="after")
    def _non_nullable_fields(self) -> ProfileUpdate:
        # These columns have defaults and semantic meaning; they can change but not be cleared.
        for name in ("difficulty_band", "language", "timezone", "preferences", "onboarding_state"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


@cache
def _timezones() -> frozenset[str]:
    return frozenset(available_timezones())
