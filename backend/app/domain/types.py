"""Reusable constrained types for request validation (SECURITY_MODEL §5.2)."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, StringConstraints


def _reject_control_chars(value: str) -> str:
    # NUL is rejected by PostgreSQL TEXT; other C0 controls (except tab/newline/CR)
    # have no place in user-entered text.
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
        raise ValueError("must not contain control characters")
    return value


def _not_blank(value: str) -> str:
    if not value:
        raise ValueError("must not be blank")
    return value


Name = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_reject_control_chars),
    AfterValidator(_not_blank),
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=5000),
    AfterValidator(_reject_control_chars),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=100),
    AfterValidator(_reject_control_chars),
]
Icon = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=32),
    AfterValidator(_reject_control_chars),
]
