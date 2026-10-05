"""Dashboard helpers: streaks, heatmap columns and point-in-time mastery."""

from __future__ import annotations

from datetime import date, timedelta

from app.services.dashboard import mastery_at, streak_days, week_columns

TODAY = date(2026, 10, 5)


def days(*offsets: int) -> set[date]:
    return {TODAY - timedelta(days=o) for o in offsets}


def test_streak_counts_back_from_today_or_yesterday() -> None:
    assert streak_days(days(0, 1, 2), TODAY) == 3
    assert streak_days(days(1, 2), TODAY) == 2  # not studied yet today: streak still alive
    assert streak_days(days(2, 3), TODAY) == 0
    assert streak_days(set(), TODAY) == 0
    assert streak_days(days(0, 2), TODAY) == 1


def test_week_columns_end_today() -> None:
    cols = week_columns(TODAY, 3)
    assert cols == [TODAY - timedelta(days=14), TODAY - timedelta(days=7), TODAY]


def test_mastery_at_uses_the_last_entry_on_or_before_the_day() -> None:
    history = [
        {"date": "2026-09-20", "mastery": 0.3, "event": "practice"},
        {"date": "2026-09-28", "mastery": 0.6, "event": "practice"},
        "garbage",
        {"date": "not-a-date", "mastery": 1.0},
        {"date": "2026-10-01", "mastery": 0.55, "event": "decay"},
    ]
    assert mastery_at(history, date(2026, 9, 19), fallback=0.5, assessed=date(2026, 9, 20)) is None
    assert mastery_at(history, date(2026, 9, 25), fallback=None, assessed=None) == 0.3
    assert mastery_at(history, TODAY, fallback=None, assessed=None) == 0.55
    # No history (seeded rows): fall back to the stored level once it was assessed.
    assert mastery_at([], TODAY, fallback=0.4, assessed=date(2026, 10, 1)) == 0.4
    assert mastery_at([], date(2026, 9, 1), fallback=0.4, assessed=date(2026, 10, 1)) is None
