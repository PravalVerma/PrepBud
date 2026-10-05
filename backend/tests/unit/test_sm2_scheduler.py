"""SM-2 spaced repetition (LEARNING_ENGINE §5.1, ADR-008)."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.student_model.review_scheduler import (
    SM2Input,
    SM2Scheduler,
    score_to_quality,
    sm2_schedule,
)

TODAY = date(2026, 10, 5)


@pytest.mark.parametrize(
    ("score", "quality"),
    [
        (1.0, 5),
        (0.95, 5),
        (0.9, 4),
        (0.8, 4),
        (0.7, 3),
        (0.6, 3),
        (0.5, 2),
        (0.4, 2),
        (0.3, 1),
        (0.2, 1),
        (0.1, 0),
        (0.0, 0),
    ],
)
def test_score_to_quality(score: float, quality: int) -> None:
    assert score_to_quality(score) == quality


def test_failed_recall_resets() -> None:
    out = sm2_schedule(
        SM2Input(quality=2, repetition_count=4, ease_factor=2.5, interval_days=20), today=TODAY
    )
    assert out.new_repetition_count == 0
    assert out.next_interval_days == 1.0
    assert out.next_review_date == date(2026, 10, 6)


def test_intervals_grow_with_consecutive_successes() -> None:
    state = SM2Input(quality=5, repetition_count=0, ease_factor=2.5, interval_days=1)
    intervals = []
    for _ in range(5):
        out = sm2_schedule(state, today=TODAY)
        intervals.append(out.next_interval_days)
        state = SM2Input(5, out.new_repetition_count, out.new_ease_factor, out.next_interval_days)
    assert intervals[:2] == [1.0, 6.0]
    assert intervals == sorted(intervals) and intervals[-1] > 6 * 2.5


def test_ease_factor_moves_with_quality_and_is_bounded() -> None:
    up = sm2_schedule(SM2Input(5, 3, 2.5, 10), today=TODAY)
    down = sm2_schedule(SM2Input(3, 3, 2.5, 10), today=TODAY)
    assert up.new_ease_factor == pytest.approx(2.6)
    assert down.new_ease_factor < 2.5
    floor = SM2Input(0, 0, 1.3, 1)
    assert sm2_schedule(floor, today=TODAY).new_ease_factor == 1.3
    assert (
        sm2_schedule(SM2Input(0, 0, 1.4, 1), today=TODAY, minimum_ease=1.35).new_ease_factor == 1.35
    )


def test_next_review_date_from_interval() -> None:
    out = sm2_schedule(SM2Input(4, 2, 2.0, 6), today=TODAY)
    assert out.next_interval_days == 12.0
    assert out.next_review_date == date(2026, 10, 17)


def test_quality_is_clamped() -> None:
    assert sm2_schedule(SM2Input(9, 0, 2.5, 1), today=TODAY).new_ease_factor == pytest.approx(2.6)
    assert sm2_schedule(SM2Input(-3, 2, 2.5, 6), today=TODAY).new_repetition_count == 0


def test_scheduler_interface_uses_performance() -> None:
    scheduler = SM2Scheduler(minimum_ease=1.3)
    good = scheduler.compute_next_review(SM2Input(0, 1, 2.5, 1), 0.9, today=TODAY)
    bad = scheduler.compute_next_review(SM2Input(0, 1, 2.5, 1), 0.3, today=TODAY)
    assert good.next_interval_days == 6.0 and good.new_repetition_count == 2
    assert bad.next_interval_days == 1.0 and bad.new_repetition_count == 0
    assert sm2_schedule(SM2Input(5, 0, 2.5, 1)).next_review_date > date(2000, 1, 1)
