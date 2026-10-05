"""Spaced repetition (LEARNING_ENGINE §5.1, ADR-008): SM-2 behind a pluggable interface.

`StudentConceptMastery` keeps everything SM-2 (and later FSRS) needs: ease factor,
interval, repetition count, last/next review timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SM2Input:
    quality: int  # 0–5
    repetition_count: int
    ease_factor: float
    interval_days: float


@dataclass(frozen=True, slots=True)
class ReviewSchedule:
    next_interval_days: float
    new_ease_factor: float
    new_repetition_count: int
    next_review_date: date


def score_to_quality(score: float) -> int:
    """Map a 0.0–1.0 score to SM-2's 0–5 quality rating."""
    if score >= 0.95:
        return 5
    if score >= 0.80:
        return 4
    if score >= 0.60:
        return 3
    if score >= 0.40:
        return 2
    if score >= 0.20:
        return 1
    return 0


def sm2_schedule(
    inp: SM2Input, *, today: date | None = None, minimum_ease: float = 1.3
) -> ReviewSchedule:
    """Classic SM-2: failed recall (quality < 3) resets the repetition count."""
    quality = max(0, min(5, inp.quality))
    if quality < 3:
        repetitions, interval = 0, 1.0
    else:
        if inp.repetition_count <= 0:
            interval = 1.0
        elif inp.repetition_count == 1:
            interval = 6.0
        else:
            interval = max(1.0, inp.interval_days) * inp.ease_factor
        repetitions = inp.repetition_count + 1
    ease = inp.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease = max(minimum_ease, ease)
    start = today or date.today()
    return ReviewSchedule(
        next_interval_days=round(interval, 2),
        new_ease_factor=round(ease, 4),
        new_repetition_count=repetitions,
        next_review_date=start + timedelta(days=int(interval)),
    )


class ReviewScheduler(Protocol):
    def compute_next_review(self, state: SM2Input, performance: float) -> ReviewSchedule: ...


class SM2Scheduler:
    def __init__(self, minimum_ease: float = 1.3) -> None:
        self.minimum_ease = minimum_ease

    def compute_next_review(
        self, state: SM2Input, performance: float, *, today: date | None = None
    ) -> ReviewSchedule:
        quality = score_to_quality(performance)
        return sm2_schedule(
            SM2Input(quality, state.repetition_count, state.ease_factor, state.interval_days),
            today=today,
            minimum_ease=self.minimum_ease,
        )
