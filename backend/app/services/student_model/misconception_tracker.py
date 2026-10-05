"""Misconception tracking (DOMAIN_MODEL aggregate 8, AC-4.6).

A detected misconception is matched to the concept's catalogue by normalised name (or
added to it), then recorded against the student with evidence. Status transitions:
``active → resolved → recurring → resolved``. A misconception is resolved once the
student answers the concept correctly several times in a row.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Misconception, StudentMisconception
from app.services.content.concept_extractor import normalize_name

MIN_CONFIDENCE = 0.5
RESOLVE_AFTER_STREAK = 3
EVIDENCE_LIMIT = 20


@dataclass(frozen=True, slots=True)
class DetectedMisconception:
    name: str
    description: str = ""
    confidence: float = 0.7
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class RecordedMisconception:
    misconception_id: uuid.UUID
    student_misconception_id: uuid.UUID
    name: str
    description: str
    status: str
    confidence: float
    occurrence_count: int


def misconception_key(name: str) -> str:
    """Matching key: models write names as snake_case or as words — treat them alike."""
    return normalize_name(name.replace("_", " "))


def display_name(name: str) -> str:
    """'sign_error_in_differentiation' → 'Sign error in differentiation'."""
    text = " ".join(name.replace("_", " ").split())
    return text[:1].upper() + text[1:] if text else "Unnamed misconception"


class MisconceptionTracker:
    def __init__(
        self, session: AsyncSession, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.session = session
        self.now = now

    async def _catalogue_entry(
        self, concept_id: uuid.UUID, detected: DetectedMisconception
    ) -> Misconception:
        key = misconception_key(detected.name)
        existing = (
            await self.session.scalars(
                select(Misconception).where(Misconception.concept_id == concept_id)
            )
        ).all()
        for item in existing:
            if misconception_key(item.name) == key:
                return item
        item = Misconception(
            concept_id=concept_id,
            name=display_name(detected.name)[:200],
            description=(detected.description or display_name(detected.name))[:2000],
            indicators=[detected.evidence[:500]] if detected.evidence else [],
            remediation_hints=[],
            source_type="detected",
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def record(
        self,
        user_id: uuid.UUID,
        concept_id: uuid.UUID,
        detected: Sequence[DetectedMisconception],
        *,
        attempt_id: uuid.UUID | None = None,
    ) -> list[RecordedMisconception]:
        """Record confident detections; returns what was recorded (caller commits).

        ``concept_id`` must already be verified as owned by ``user_id``.
        """
        recorded: list[RecordedMisconception] = []
        seen: set[str] = set()
        now = self.now()
        for item in detected:
            key = misconception_key(item.name)
            if item.confidence < MIN_CONFIDENCE or not key or key in seen:
                continue
            seen.add(key)
            catalogue = await self._catalogue_entry(concept_id, item)
            evidence: dict[str, Any] = {
                "attempt_id": str(attempt_id) if attempt_id else None,
                "description": item.evidence[:500],
                "detected_at": now.isoformat(),
            }
            student = await self.session.scalar(
                select(StudentMisconception).where(
                    StudentMisconception.user_id == user_id,
                    StudentMisconception.misconception_id == catalogue.id,
                )
            )
            if student is None:
                student = StudentMisconception(
                    user_id=user_id,
                    misconception_id=catalogue.id,
                    status="active",
                    evidence=[evidence],
                    occurrence_count=1,
                    detected_at=now,
                )
                self.session.add(student)
            else:
                student.occurrence_count = int(student.occurrence_count or 0) + 1
                student.evidence = [*(student.evidence or []), evidence][-EVIDENCE_LIMIT:]
                if student.status == "resolved":
                    student.status = "recurring"
                    student.resolved_at = None
            await self.session.flush()
            recorded.append(
                RecordedMisconception(
                    misconception_id=catalogue.id,
                    student_misconception_id=student.id,
                    name=catalogue.name,
                    description=catalogue.description,
                    status=student.status or "active",
                    confidence=round(item.confidence, 3),
                    occurrence_count=int(student.occurrence_count or 1),
                )
            )
        return recorded

    async def resolve_for_concept(self, user_id: uuid.UUID, concept_id: uuid.UUID) -> int:
        """Mark the student's active/recurring misconceptions on a concept as resolved."""
        rows = (
            await self.session.scalars(
                select(StudentMisconception)
                .join(Misconception, Misconception.id == StudentMisconception.misconception_id)
                .where(
                    StudentMisconception.user_id == user_id,
                    Misconception.concept_id == concept_id,
                    StudentMisconception.status.in_(("active", "recurring")),
                )
            )
        ).all()
        for row in rows:
            row.status = "resolved"
            row.resolved_at = self.now()
        await self.session.flush()
        return len(rows)

    async def known_for_concept(
        self, user_id: uuid.UUID, concept_id: uuid.UUID
    ) -> list[dict[str, str]]:
        """The student's unresolved misconceptions on a concept (for tutor context)."""
        rows = await self.session.execute(
            select(Misconception.name, Misconception.description, StudentMisconception.status)
            .join(StudentMisconception, StudentMisconception.misconception_id == Misconception.id)
            .where(
                StudentMisconception.user_id == user_id,
                Misconception.concept_id == concept_id,
                StudentMisconception.status.in_(("active", "recurring")),
            )
        )
        return [{"name": r[0], "description": r[1], "status": r[2] or "active"} for r in rows]
