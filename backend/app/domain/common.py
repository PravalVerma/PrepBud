"""Response envelope and shared schema primitives (API_CONTRACT §1, §4)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 20


class Pagination(BaseModel):
    total: int
    page: int
    per_page: int
    total_pages: int

    @classmethod
    def build(cls, *, total: int, page: int, per_page: int) -> Pagination:
        return cls(
            total=total,
            page=page,
            per_page=per_page,
            total_pages=math.ceil(total / per_page) if total else 0,
        )


class Meta(BaseModel):
    request_id: str | None = None
    timestamp: datetime


class PaginatedMeta(Meta):
    pagination: Pagination


class Envelope[T](BaseModel):
    data: T
    meta: Meta


class PaginatedEnvelope[T](BaseModel):
    data: list[T]
    meta: PaginatedMeta


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    meta: Meta


class ORMModel(BaseModel):
    """Base for read schemas built from SQLAlchemy objects."""

    model_config = ConfigDict(from_attributes=True)


def _request_id(request: Request | None) -> str | None:
    return getattr(request.state, "request_id", None) if request is not None else None


def build_meta(request: Request | None) -> Meta:
    return Meta(request_id=_request_id(request), timestamp=datetime.now(UTC))


def envelope[T](request: Request, data: T) -> Envelope[T]:
    return Envelope[T](data=data, meta=build_meta(request))


def paginated[T](
    request: Request, data: list[T], *, total: int, page: int, per_page: int
) -> PaginatedEnvelope[T]:
    return PaginatedEnvelope[T](
        data=data,
        meta=PaginatedMeta(
            request_id=_request_id(request),
            timestamp=datetime.now(UTC),
            pagination=Pagination.build(total=total, page=page, per_page=per_page),
        ),
    )


# --- Mastery value object (DOMAIN_MODEL §5) -----------------------------------

MASTERY_LABELS: tuple[tuple[float, str], ...] = (
    (0.2, "novice"),
    (0.4, "beginner"),
    (0.6, "intermediate"),
    (0.8, "proficient"),
)


def mastery_label(level: float) -> str:
    """Map a mastery level in [0, 1] to its semantic label.

    Ranges are half-open on the upper bound except the last (0.8–1.0 = mastered).
    """
    if not 0.0 <= level <= 1.0:
        raise ValueError(f"Mastery level must be within [0.0, 1.0], got {level}")
    for upper, label in MASTERY_LABELS:
        if level < upper:
            return label
    return "mastered"
