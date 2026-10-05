"""Student model: per-concept mastery (LEARNING_ENGINE §4–5, DOMAIN_MODEL aggregate 7).

* `update_mastery` — Bayesian Knowledge Tracing, simplified as in LEARNING_ENGINE §4.2,
  with two corrections so the documented guarantees hold (AC-4.1, AC-4.2):
    - partial credit is soft evidence (a mix of the "correct" and "incorrect" posteriors),
      and the learning transition is weighted by that evidence — a student does not
      "learn" from a wrong answer the way they do from a right one;
    - the result is monotonic: a correct answer never lowers mastery and an incorrect one
      never raises it (the raw formula can raise mastery after a wrong answer when mastery
      is very low).
* `apply_mastery_decay` — Ebbinghaus forgetting curve (§5.2). It is applied on *read*,
  from ``last_assessed_at``, so it never compounds; an update starts from the decayed
  value and resets the clock.
* `MasteryTracker` — reads snapshots (Redis cache ``mastery:{user}:{concept}``, 30 min)
  and records attempts with row locks. One row per (user, concept).
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import LearningEngineSettings
from app.core.logging import get_logger
from app.db.models import StudentConceptMastery
from app.services.student_model.review_scheduler import ReviewSchedule, SM2Input, SM2Scheduler

logger = get_logger(__name__)

_MAX_P = 0.999  # keep BKT away from the absorbing boundary so evidence still moves it


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class MasteryUpdateInput:
    current_mastery: float
    is_correct: bool
    score: float
    question_difficulty: float
    slip_rate: float = 0.10
    guess_rate: float = 0.25
    learning_rate: float = 0.10


def update_mastery(inp: MasteryUpdateInput) -> float:
    current = _clamp(inp.current_mastery)
    p = min(current, _MAX_P)
    slip, guess = _clamp(inp.slip_rate, 0.0, 0.99), _clamp(inp.guess_rate, 0.0, 0.99)

    p_correct = p * (1 - slip) + (1 - p) * guess
    known_if_correct = (p * (1 - slip)) / p_correct if p_correct > 0 else p
    p_incorrect = p * slip + (1 - p) * (1 - guess)
    known_if_incorrect = (p * slip) / p_incorrect if p_incorrect > 0 else p

    # How strongly the response counts as "correct" evidence.
    score = _clamp(inp.score)
    evidence = max(score, 0.5) if inp.is_correct else min(score, 0.5)
    posterior = evidence * known_if_correct + (1 - evidence) * known_if_incorrect
    learned = posterior + (1 - posterior) * _clamp(inp.learning_rate) * evidence

    # Harder questions carry more signal.
    weight = 0.5 + 0.5 * _clamp(inp.question_difficulty)
    updated = current * (1 - weight) + learned * weight
    updated = max(updated, current) if inp.is_correct else min(updated, current)
    return _clamp(updated)


def apply_mastery_decay(mastery: float, days_since_review: float, ease_factor: float) -> float:
    """Ebbinghaus-inspired forgetting: higher ease = slower decay."""
    if days_since_review <= 0:
        return _clamp(mastery)
    stability = max(ease_factor, 1.3) * 10
    return _clamp(mastery * math.exp(-days_since_review / stability))


def confidence_for(attempts: int) -> float:
    """Confidence in the estimate grows with evidence: 0 → 0, 3 → 0.5, 9 → 0.75."""
    return round(attempts / (attempts + 3), 4) if attempts > 0 else 0.0


# --- Tracker -------------------------------------------------------------------------------


@dataclass(slots=True)
class MasterySnapshot:
    concept_id: uuid.UUID
    level: float  # effective (decayed) mastery used for decisions
    stored_level: float
    confidence: float = 0.0
    attempt_count: int = 0
    correct_count: int = 0
    streak: int = 0
    ease_factor: float = 2.5
    interval_days: float = 1.0
    repetition_count: int = 0
    last_assessed_at: datetime | None = None
    next_review_at: datetime | None = None

    def to_cache(self) -> str:
        data = asdict(self)
        data["concept_id"] = str(self.concept_id)
        for key in ("last_assessed_at", "next_review_at"):
            value = data[key]
            data[key] = value.isoformat() if value else None
        return json.dumps(data)

    @classmethod
    def from_cache(cls, raw: str) -> MasterySnapshot:
        data: dict[str, Any] = json.loads(raw)
        data["concept_id"] = uuid.UUID(data["concept_id"])
        for key in ("last_assessed_at", "next_review_at"):
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class MasteryChange:
    concept_id: uuid.UUID
    old_level: float
    new_level: float
    streak: int
    attempt_count: int

    @property
    def delta(self) -> float:
        return round(self.new_level - self.old_level, 4)


def cache_key(user_id: uuid.UUID, concept_id: uuid.UUID) -> str:
    return f"mastery:{user_id}:{concept_id}"


class MasteryTracker:
    def __init__(
        self,
        session: AsyncSession,
        settings: LearningEngineSettings,
        *,
        redis: Redis | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.settings = settings
        self.redis = redis
        self.now = now

    def _effective(self, stored: float, last: datetime | None, ease: float) -> float:
        if not self.settings.decay_enabled or last is None:
            return _clamp(stored)
        days = (self.now() - last).total_seconds() / 86_400
        return apply_mastery_decay(stored, days, ease)

    def _snapshot(self, row: StudentConceptMastery) -> MasterySnapshot:
        stored = float(row.mastery_level or 0.0)
        ease = float(row.ease_factor or self.settings.sm2_initial_ease_factor)
        return MasterySnapshot(
            concept_id=row.concept_id,
            level=round(self._effective(stored, row.last_assessed_at, ease), 4),
            stored_level=stored,
            confidence=float(row.confidence or 0.0),
            attempt_count=int(row.attempt_count or 0),
            correct_count=int(row.correct_count or 0),
            streak=int(row.streak or 0),
            ease_factor=ease,
            interval_days=float(row.interval_days or 1.0),
            repetition_count=int(row.repetition_count or 0),
            last_assessed_at=row.last_assessed_at,
            next_review_at=row.next_review_at,
        )

    def _empty(self, concept_id: uuid.UUID) -> MasterySnapshot:
        return MasterySnapshot(
            concept_id=concept_id,
            level=0.0,
            stored_level=0.0,
            ease_factor=self.settings.sm2_initial_ease_factor,
        )

    async def _cache_get(
        self, user_id: uuid.UUID, ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, MasterySnapshot]:
        if self.redis is None or not ids:
            return {}
        try:
            raw = await self.redis.mget([cache_key(user_id, c) for c in ids])
        except Exception as exc:
            logger.warning("mastery cache unavailable", extra={"error": type(exc).__name__})
            return {}
        found = {}
        for concept_id, value in zip(ids, raw, strict=True):
            if value:
                snap = MasterySnapshot.from_cache(value)
                # Recompute decay against "now" — cached values are raw.
                snap.level = round(
                    self._effective(snap.stored_level, snap.last_assessed_at, snap.ease_factor), 4
                )
                found[concept_id] = snap
        return found

    async def _cache_set(self, user_id: uuid.UUID, snaps: Iterable[MasterySnapshot]) -> None:
        if self.redis is None:
            return
        try:
            async with self.redis.pipeline(transaction=False) as pipe:
                for snap in snaps:
                    pipe.set(
                        cache_key(user_id, snap.concept_id),
                        snap.to_cache(),
                        ex=self.settings.mastery_cache_ttl_seconds,
                    )
                await pipe.execute()
        except Exception as exc:
            logger.warning("mastery cache unavailable", extra={"error": type(exc).__name__})

    async def snapshot(
        self, user_id: uuid.UUID, concept_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, MasterySnapshot]:
        """Effective mastery for each concept (0.0 for concepts never assessed)."""
        ids = list(dict.fromkeys(concept_ids))
        result = await self._cache_get(user_id, ids)
        missing = [c for c in ids if c not in result]
        if missing:
            rows = (
                await self.session.scalars(
                    select(StudentConceptMastery).where(
                        StudentConceptMastery.user_id == user_id,
                        StudentConceptMastery.concept_id.in_(missing),
                    )
                )
            ).all()
            fetched = {r.concept_id: self._snapshot(r) for r in rows}
            await self._cache_set(user_id, fetched.values())
            for concept_id in missing:
                result[concept_id] = fetched.get(concept_id) or self._empty(concept_id)
        return result

    async def levels(
        self, user_id: uuid.UUID, concept_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, float]:
        return {c: s.level for c, s in (await self.snapshot(user_id, concept_ids)).items()}

    async def _locked_row(self, user_id: uuid.UUID, concept_id: uuid.UUID) -> StudentConceptMastery:
        await self.session.execute(
            insert(StudentConceptMastery)
            .values(
                user_id=user_id,
                concept_id=concept_id,
                ease_factor=self.settings.sm2_initial_ease_factor,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "concept_id"])
        )
        row = await self.session.scalar(
            select(StudentConceptMastery)
            .where(
                StudentConceptMastery.user_id == user_id,
                StudentConceptMastery.concept_id == concept_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:  # pragma: no cover - inserted just above
            raise RuntimeError("mastery row disappeared")
        return row

    async def record_attempt(
        self,
        user_id: uuid.UUID,
        concept_id: uuid.UUID,
        *,
        is_correct: bool,
        score: float,
        difficulty: float,
        event: str = "practice",
    ) -> MasteryChange:
        """Apply one assessed answer (caller commits)."""
        cfg = self.settings
        row = await self._locked_row(user_id, concept_id)
        ease = float(row.ease_factor or cfg.sm2_initial_ease_factor)
        old = self._effective(float(row.mastery_level or 0.0), row.last_assessed_at, ease)
        new = update_mastery(
            MasteryUpdateInput(
                current_mastery=old,
                is_correct=is_correct,
                score=score,
                question_difficulty=difficulty,
                slip_rate=cfg.bkt_slip_rate,
                guess_rate=cfg.bkt_guess_rate,
                learning_rate=cfg.bkt_learning_rate,
            )
        )
        now = self.now()
        attempts = int(row.attempt_count or 0) + 1
        row.mastery_level = round(new, 6)
        row.attempt_count = attempts
        row.correct_count = int(row.correct_count or 0) + (1 if is_correct else 0)
        row.streak = int(row.streak or 0) + 1 if is_correct else 0
        row.confidence = confidence_for(attempts)
        row.last_assessed_at = now
        history = list(row.history or [])
        history.append({"date": now.date().isoformat(), "mastery": round(new, 4), "event": event})
        row.history = history[-cfg.mastery_history_limit :]
        await self.session.flush()
        await self._cache_set(user_id, [self._snapshot(row)])
        return MasteryChange(concept_id, round(old, 4), round(new, 4), int(row.streak), attempts)

    async def schedule_review(
        self,
        user_id: uuid.UUID,
        concept_id: uuid.UUID,
        performance: float,
        *,
        today: date | None = None,
    ) -> ReviewSchedule:
        """SM-2 after a session: next review date from the session's performance on the concept."""
        row = await self._locked_row(user_id, concept_id)
        scheduler = SM2Scheduler(self.settings.sm2_minimum_ease_factor)
        schedule = scheduler.compute_next_review(
            SM2Input(
                quality=0,
                repetition_count=int(row.repetition_count or 0),
                ease_factor=float(row.ease_factor or self.settings.sm2_initial_ease_factor),
                interval_days=float(row.interval_days or 1.0),
            ),
            performance,
            today=today or self.now().date(),
        )
        row.ease_factor = schedule.new_ease_factor
        row.interval_days = schedule.next_interval_days
        row.repetition_count = schedule.new_repetition_count
        row.next_review_at = datetime.combine(
            schedule.next_review_date, datetime.min.time(), tzinfo=UTC
        )
        await self.session.flush()
        await self._cache_set(user_id, [self._snapshot(row)])
        return schedule
