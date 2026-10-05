"""Student model persistence: mastery records, cache, decay, SM-2, misconceptions
(DATA_MODEL §3.17–3.19, AC-4.1, AC-4.2, AC-4.6)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Concept, Misconception, StudentConceptMastery, StudentMisconception
from app.services.student_model.mastery_tracker import MasteryTracker, cache_key
from app.services.student_model.misconception_tracker import (
    DetectedMisconception,
    MisconceptionTracker,
    display_name,
)
from tests.integration.conftest import provision
from tests.support import auth

H = auth("student-model")


@pytest.fixture
async def concept(client: httpx.AsyncClient, db: AsyncSession) -> Concept:
    user_id = await provision(client, H)
    c = Concept(user_id=user_id, name="Limits", difficulty_estimate=0.4)
    db.add(c)
    await db.commit()
    return c


def tracker(app: FastAPI, db: AsyncSession, now: datetime | None = None) -> MasteryTracker:
    clock = (lambda: now) if now else (lambda: datetime.now(UTC))
    return MasteryTracker(db, app.state.settings.learning_engine, redis=app.state.redis, now=clock)


class TestMastery:
    async def test_attempts_update_one_row_per_concept(
        self, app: FastAPI, db: AsyncSession, concept: Concept
    ) -> None:
        t = tracker(app, db)
        up = await t.record_attempt(
            concept.user_id, concept.id, is_correct=True, score=1.0, difficulty=0.5
        )
        await db.commit()
        down = await t.record_attempt(
            concept.user_id, concept.id, is_correct=False, score=0.0, difficulty=0.5
        )
        await db.commit()

        assert up.new_level > up.old_level == 0.0  # AC-4.1
        assert down.new_level < down.old_level
        rows = (await db.scalars(select(StudentConceptMastery))).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.attempt_count == 2 and row.correct_count == 1 and row.streak == 0
        assert 0.0 <= (row.mastery_level or 0) <= 1.0  # AC-4.2
        assert row.confidence == pytest.approx(0.4)
        assert [h["event"] for h in row.history or []] == ["practice", "practice"]
        assert row.last_assessed_at is not None

    async def test_streak_and_history_limit(
        self, app: FastAPI, db: AsyncSession, concept: Concept
    ) -> None:
        t = tracker(app, db)
        for _ in range(55):
            change = await t.record_attempt(
                concept.user_id,
                concept.id,
                is_correct=True,
                score=1.0,
                difficulty=0.9,
                event="review",
            )
        await db.commit()
        row = await db.scalar(select(StudentConceptMastery))
        assert row is not None and change.streak == 55 and row.streak == 55
        assert len(row.history or []) == app.state.settings.learning_engine.mastery_history_limit
        assert row.mastery_level is not None and row.mastery_level > 0.95

    async def test_snapshot_defaults_cache_and_decay(
        self, app: FastAPI, db: AsyncSession, concept: Concept
    ) -> None:
        unseen = uuid.uuid4()
        t = tracker(app, db)
        snap = await t.snapshot(concept.user_id, [concept.id, unseen])
        assert snap[unseen].level == 0.0 and snap[concept.id].attempt_count == 0

        await t.record_attempt(
            concept.user_id, concept.id, is_correct=True, score=1.0, difficulty=1.0
        )
        await db.commit()
        cached = json.loads(await app.state.redis.get(cache_key(concept.user_id, concept.id)))
        fresh = (await t.snapshot(concept.user_id, [concept.id]))[concept.id]
        assert cached["stored_level"] == pytest.approx(fresh.stored_level)
        assert fresh.level == pytest.approx(fresh.stored_level, abs=1e-3)

        month_later = tracker(app, db, now=datetime.now(UTC) + timedelta(days=30))
        decayed = (await month_later.snapshot(concept.user_id, [concept.id]))[concept.id]
        assert decayed.level < fresh.level  # decay applied on read (from the cache too)
        assert decayed.stored_level == fresh.stored_level  # …but never written back

        # An attempt starts from the decayed value and resets the clock.
        change = await month_later.record_attempt(
            concept.user_id, concept.id, is_correct=True, score=1.0, difficulty=0.5
        )
        assert change.old_level == pytest.approx(decayed.level, abs=1e-3)

    async def test_works_without_redis(
        self, app: FastAPI, db: AsyncSession, concept: Concept
    ) -> None:
        t = MasteryTracker(db, app.state.settings.learning_engine, redis=None)
        await t.record_attempt(
            concept.user_id, concept.id, is_correct=True, score=1.0, difficulty=0.5
        )
        assert (await t.levels(concept.user_id, [concept.id]))[concept.id] > 0

    async def test_schedule_review(self, app: FastAPI, db: AsyncSession, concept: Concept) -> None:
        today = datetime(2026, 10, 5, tzinfo=UTC)
        t = tracker(app, db, now=today)
        first = await t.schedule_review(concept.user_id, concept.id, 0.9)
        second = await t.schedule_review(concept.user_id, concept.id, 1.0)
        failed = await t.schedule_review(concept.user_id, concept.id, 0.1)
        await db.commit()
        assert first.next_interval_days == 1.0 and second.next_interval_days == 6.0
        assert failed.next_interval_days == 1.0 and failed.new_repetition_count == 0
        row = await db.scalar(select(StudentConceptMastery))
        assert row is not None and row.next_review_at == datetime(2026, 10, 6, tzinfo=UTC)
        assert row.repetition_count == 0 and (row.ease_factor or 0) < 2.5


class TestMisconceptions:
    async def test_record_recurring_and_resolve(self, db: AsyncSession, concept: Concept) -> None:
        tr = MisconceptionTracker(db)
        sign = DetectedMisconception("sign_error", "Flips the sign", 0.9, "-2x")
        first = await tr.record(
            concept.user_id, concept.id, [sign, DetectedMisconception("guess", confidence=0.3)]
        )
        await db.commit()
        assert [r.name for r in first] == ["Sign error"]  # low-confidence detection ignored
        assert first[0].status == "active"

        again = await tr.record(
            concept.user_id, concept.id, [DetectedMisconception("Sign Error", "", 0.8, "-6x")]
        )
        await db.commit()
        assert again[0].misconception_id == first[0].misconception_id  # matched by normalised name
        assert again[0].occurrence_count == 2

        assert await tr.resolve_for_concept(concept.user_id, concept.id) == 1
        recurring = await tr.record(concept.user_id, concept.id, [sign])
        await db.commit()
        assert recurring[0].status == "recurring"

        row = await db.scalar(select(StudentMisconception))
        assert row is not None and row.occurrence_count == 3 and len(row.evidence or []) == 3
        assert await db.scalar(select(func.count()).select_from(Misconception)) == 1
        assert (await tr.known_for_concept(concept.user_id, concept.id))[0]["status"] == "recurring"

    async def test_duplicates_in_one_call_counted_once(
        self, db: AsyncSession, concept: Concept
    ) -> None:
        tr = MisconceptionTracker(db)
        recorded = await tr.record(
            concept.user_id,
            concept.id,
            [DetectedMisconception("x_error"), DetectedMisconception("X error")],
        )
        assert len(recorded) == 1

    def test_display_name(self) -> None:
        assert display_name("sign_error_in_differentiation") == "Sign error in differentiation"
        assert display_name("  ") == "Unnamed misconception"
