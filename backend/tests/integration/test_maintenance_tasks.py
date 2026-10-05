"""Daily maintenance Celery tasks through the worker runtime (AC-6.5). Tasks run eagerly
(`.apply()`), each in its own event loop as in a worker process, so these tests are sync."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from app.db.base import Base
from app.db.models import Concept, StudentConceptMastery, StudyPlan, User
from app.workers import maintenance_tasks
from app.workers.celery_app import celery_app
from app.workers.maintenance_tasks import daily_maintenance, refresh_user
from tests.integration.conftest import Infra
from tests.integration.test_worker_tasks import _exec
from tests.support import build_settings


@pytest.fixture
def maintenance_env(infra: Infra, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    settings = build_settings(database_url=infra.database_url, redis_url=infra.redis_url)
    monkeypatch.setattr(maintenance_tasks, "get_settings", lambda: settings)
    yield {"settings": settings}
    tables = ", ".join(Base.metadata.tables)
    asyncio.run(_exec(infra.database_url, text(f"TRUNCATE TABLE {tables} CASCADE")))


def seed_learner(url: str, days_ago: int = 30) -> tuple[uuid.UUID, uuid.UUID]:
    user_id, concept_id = uuid.uuid4(), uuid.uuid4()
    assessed = datetime.now(UTC) - timedelta(days=days_ago)
    asyncio.run(
        _exec(
            url,
            User.__table__.insert().values(
                id=user_id, auth_id=f"m-{user_id}", email=f"{user_id}@example.com"
            ),
            Concept.__table__.insert().values(id=concept_id, user_id=user_id, name="Limits"),
            StudentConceptMastery.__table__.insert().values(
                user_id=user_id,
                concept_id=concept_id,
                mastery_level=0.95,
                ease_factor=2.5,
                attempt_count=5,
                last_assessed_at=assessed,
                history=[
                    {"date": assessed.date().isoformat(), "mastery": 0.95, "event": "practice"}
                ],
            ),
        )
    )
    return user_id, concept_id


def stored_level(url: str, concept_id: uuid.UUID) -> float:
    [result] = asyncio.run(
        _exec(
            url,
            select(StudentConceptMastery.mastery_level).where(
                StudentConceptMastery.concept_id == concept_id
            ),
        )
    )
    return float(result.scalar_one())


def test_refresh_user_materialises_decay_once(maintenance_env: dict[str, Any]) -> None:
    url = maintenance_env["settings"].database_url
    user_id, concept_id = seed_learner(url)

    result = refresh_user.apply(args=[str(user_id)]).get()
    assert result == {"user_id": str(user_id), "decayed": 1, "overdue": 0}
    level = stored_level(url, concept_id)
    assert level == pytest.approx(0.95 * 2.718281828 ** (-30 / 25), abs=0.01)

    refresh_user.apply(args=[str(user_id)]).get()  # idempotent: no compounding
    assert stored_level(url, concept_id) == pytest.approx(level, abs=0.001)
    [plans] = asyncio.run(_exec(url, select(StudyPlan.status)))
    assert sorted(plans.scalars().all()) == ["active", "superseded"]


def test_daily_fans_out_per_learner(
    maintenance_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    url = maintenance_env["settings"].database_url
    first, _ = seed_learner(url)
    second, _ = seed_learner(url, days_ago=2)
    sent: list[str] = []
    monkeypatch.setattr(refresh_user, "delay", lambda user_id: sent.append(user_id))

    assert daily_maintenance.apply().get() == 2
    assert sorted(sent) == sorted([str(first), str(second)])


def test_beat_schedules_the_daily_job() -> None:
    entry = celery_app.conf.beat_schedule["daily-maintenance"]
    assert entry["task"] == maintenance_tasks.DAILY_MAINTENANCE
    assert "maintenance_tasks" in " ".join(celery_app.conf.include)
