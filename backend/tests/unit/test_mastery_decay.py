"""Materialised decay (LEARNING_ENGINE §5.2, AC-6.5): never compounds, keeps history readable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.config import LearningEngineSettings
from app.db.models import StudentConceptMastery
from app.services.student_model.mastery_tracker import (
    MasterySnapshot,
    MasteryTracker,
    apply_mastery_decay,
    assessed_level,
    decay_anchor,
)

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)


def row(level: float = 0.9, history: list[Any] | None = None) -> StudentConceptMastery:
    return StudentConceptMastery(
        user_id=uuid.uuid4(),
        concept_id=uuid.uuid4(),
        mastery_level=level,
        ease_factor=2.5,
        last_assessed_at=T0,
        history=history
        if history is not None
        else [{"date": "2026-09-01", "mastery": level, "event": "practice"}],
    )


def tracker(now: datetime, **cfg: Any) -> MasteryTracker:
    return MasteryTracker(None, LearningEngineSettings(**cfg), now=lambda: now)  # type: ignore[arg-type]


def effective(r: StudentConceptMastery, now: datetime) -> float:
    return tracker(now)._snapshot(r).level


def test_materialising_never_compounds() -> None:
    r = row()
    day10, day20 = T0 + timedelta(days=10), T0 + timedelta(days=20)
    expected = apply_mastery_decay(0.9, 20, 2.5)
    assert tracker(day10).materialise_decay(r)
    assert r.mastery_level == pytest.approx(apply_mastery_decay(0.9, 10, 2.5), abs=1e-5)
    # On read at day 20 equals decaying the original value for 20 days.
    assert effective(r, day20) == pytest.approx(expected, abs=1e-4)
    assert tracker(day20).materialise_decay(r)
    assert r.mastery_level == pytest.approx(expected, abs=1e-5)
    assert effective(r, day20) == pytest.approx(expected, abs=1e-4)


def test_rerun_same_moment_is_a_no_op_and_history_steps() -> None:
    r = row()
    t = tracker(T0 + timedelta(days=1))
    assert t.materialise_decay(r)
    first = list(r.history or [])
    assert not t.materialise_decay(r)  # nothing left to decay at this moment
    assert r.history == first
    assert first[-1]["event"] == "decay" and first[-1]["from"] == 0.9

    # Small daily drops update the trailing entry; a 5-point drop starts a new one.
    for day in range(2, 30):
        tracker(T0 + timedelta(days=day)).materialise_decay(r)
    decay_entries = [e for e in r.history or [] if e["event"] == "decay"]
    assert 3 <= len(decay_entries) < 28
    drops = [e["from"] - e["mastery"] for e in decay_entries if e.get("closed")]
    assert drops and all(d >= 0.05 - 1e-9 for d in drops)


def test_nothing_to_do() -> None:
    never = row()
    never.last_assessed_at = None
    assert not tracker(T0 + timedelta(days=9)).materialise_decay(never)
    assert not tracker(T0 + timedelta(days=9), decay_enabled=False).materialise_decay(row())
    assert not tracker(T0).materialise_decay(row())  # no time has passed


def test_anchor_and_assessed_level_helpers() -> None:
    later = T0 + timedelta(days=3)
    history = [
        {"date": "2026-09-01", "mastery": 0.9, "event": "practice"},
        {"date": "2026-09-04", "mastery": 0.8, "event": "decay", "at": later.isoformat()},
    ]
    assert decay_anchor(T0, history) == later
    assert decay_anchor(later + timedelta(days=1), history) == later + timedelta(days=1)
    assert decay_anchor(T0, [{"event": "decay", "at": "garbage"}]) == T0
    assert decay_anchor(None, [{"event": "decay", "at": "2026-09-04T00:00:00"}]) == datetime(
        2026, 9, 4, tzinfo=UTC
    )
    assert assessed_level(history, 0.0) == 0.9
    assert assessed_level([{"event": "practice", "mastery": "x"}], 0.3) == 0.3
    assert assessed_level(None, 0.4) == 0.4


def test_snapshot_cache_round_trip_and_old_entries() -> None:
    snap = MasterySnapshot(
        concept_id=uuid.uuid4(),
        level=0.5,
        stored_level=0.6,
        last_assessed_at=T0,
        decay_anchor=T0 + timedelta(days=1),
        assessed_level=0.7,
    )
    assert MasterySnapshot.from_cache(snap.to_cache()) == snap
    legacy = snap.to_cache().replace('"decay_anchor"', '"ignored_field"')
    restored = MasterySnapshot.from_cache(legacy)
    assert restored.decay_anchor == T0  # falls back to last_assessed_at
