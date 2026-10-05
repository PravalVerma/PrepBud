"""Mastery dashboard, misconceptions and AI usage (API_CONTRACT §3.11–3.13; AC-7.1, AC-7.3)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AIInteraction,
    AITrace,
    Concept,
    LearningSession,
    Misconception,
    Question,
    QuestionAttempt,
    StudentConceptMastery,
    StudentMisconception,
    StudentProfile,
    Subject,
)
from app.services.dashboard import invalidate_dashboard
from tests.integration.conftest import provision
from tests.support import auth

API = "/api/v1"
H = auth("dashboard")
NOW = datetime.now(UTC)


async def get(client: httpx.AsyncClient, path: str, headers: dict[str, str] = H) -> Any:
    resp = await client.get(f"{API}{path}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.fixture
async def world(client: httpx.AsyncClient, db: AsyncSession) -> dict[str, Any]:
    user = await provision(client, H)
    maths, empty = Subject(user_id=user, name="Maths"), Subject(user_id=user, name="Zoology")
    db.add_all([maths, empty])
    await db.flush()
    limits = Concept(user_id=user, name="Limits", subject_id=maths.id)
    vectors = Concept(user_id=user, name="Vectors", subject_id=maths.id)
    loose = Concept(user_id=user, name="Loose idea")  # no subject → "Unsorted"
    db.add_all([limits, vectors, loose])
    await db.flush()
    db.add_all(
        [
            StudentConceptMastery(
                user_id=user,
                concept_id=limits.id,
                mastery_level=0.85,
                attempt_count=6,
                last_assessed_at=NOW - timedelta(days=10),
                history=[
                    {
                        "date": (NOW - timedelta(days=20)).date().isoformat(),
                        "mastery": 0.3,
                        "event": "practice",
                    },
                    {
                        "date": (NOW - timedelta(days=10)).date().isoformat(),
                        "mastery": 0.85,
                        "event": "practice",
                    },
                ],
            ),
            StudentConceptMastery(
                user_id=user,
                concept_id=vectors.id,
                mastery_level=0.2,
                attempt_count=3,
                last_assessed_at=NOW,
                history=[],
            ),
        ]
    )
    question = Question(
        user_id=user,
        concept_id=limits.id,
        question_type="true_false",
        content="?",
        correct_answer={"value": True},
    )
    q2 = Question(
        user_id=user,
        concept_id=vectors.id,
        question_type="true_false",
        content="?",
        correct_answer={"value": True},
    )
    db.add_all([question, q2])
    await db.flush()
    for days_ago, correct, q in [
        (0, True, question),
        (0, False, q2),
        (1, True, question),
        (5, True, question),
    ]:
        db.add(
            QuestionAttempt(
                question_id=q.id,
                user_id=user,
                student_response="x",
                is_correct=correct,
                score=1.0 if correct else 0.0,
                attempted_at=NOW - timedelta(days=days_ago),
            )
        )
    for days_ago, seconds in [(0, 600), (1, 1200)]:
        db.add(
            LearningSession(
                user_id=user,
                session_type="mixed",
                status="completed",
                interaction_count=4,
                started_at=NOW - timedelta(days=days_ago),
                duration_seconds=seconds,
            )
        )
    sign = Misconception(concept_id=limits.id, name="Sign error", description="Flips the sign")
    order = Misconception(concept_id=vectors.id, name="Order confusion", description="Swaps order")
    db.add_all([sign, order])
    await db.flush()
    db.add_all(
        [
            StudentMisconception(
                user_id=user,
                misconception_id=sign.id,
                status="resolved",
                occurrence_count=2,
                detected_at=NOW - timedelta(days=3),
                resolved_at=NOW,
            ),
            StudentMisconception(
                user_id=user,
                misconception_id=order.id,
                status="active",
                occurrence_count=3,
                detected_at=NOW - timedelta(days=4),
                evidence=[
                    {
                        "attempt_id": None,
                        "description": "first",
                        "detected_at": "2026-10-01T00:00:00+00:00",
                    },
                    {
                        "attempt_id": None,
                        "description": "latest",
                        "detected_at": "2026-10-02T00:00:00+00:00",
                    },
                ],
            ),
        ]
    )
    trace = AITrace(user_id=user, operation="session_begin")
    db.add(trace)
    await db.flush()
    for purpose, model, cost, days_ago, status in [
        ("generate_question", "light", 0.01, 0, "success"),
        ("generate_question", "light", 0.02, 0, "error"),
        ("tutor_explanation", "heavy", 0.10, 0, "success"),
        ("concept_extraction", "heavy", 0.50, 3, "success"),
        ("concept_extraction", "heavy", 1.00, 20, "success"),
    ]:
        db.add(
            AIInteraction(
                trace_id=trace.id,
                user_id=user,
                purpose=purpose,
                provider="fake",
                model=model,
                input_tokens=100,
                output_tokens=50,
                cost_estimate=cost,
                status=status,
                created_at=NOW - timedelta(days=days_ago),
            )
        )
    await db.commit()
    return {
        "user": user,
        "maths": maths,
        "empty": empty,
        "limits": limits,
        "vectors": vectors,
        "loose": loose,
    }


class TestOverview:
    async def test_overview(self, client: httpx.AsyncClient, world: dict[str, Any]) -> None:
        """AC-7.1: accurate mastery across subjects."""
        data = await get(client, "/mastery/overview?days=7")
        subjects = {s["name"]: s for s in data["subjects"]}
        assert list(subjects) == ["Maths", "Zoology", "Unsorted"]
        assert subjects["Maths"] == {
            "id": str(world["maths"].id),
            "name": "Maths",
            "avg_mastery": 0.525,
            "concept_count": 2,
            "mastered_count": 1,
            "struggling_count": 1,
        }
        assert subjects["Zoology"]["concept_count"] == 0
        assert subjects["Unsorted"]["id"] is None and subjects["Unsorted"]["concept_count"] == 1
        stats = data["overall_stats"]
        assert stats == {
            "total_concepts": 3,
            "total_mastered": 1,
            "in_progress": 1,
            "avg_mastery": pytest.approx(0.35, abs=1e-4),
            "streak_days": 2,
            "total_study_minutes": 30.0,
            "total_sessions": 2,
            "questions_answered": 4,
            "accuracy": 0.75,
        }
        assert data["mastery_distribution"] == {
            "novice": 1,
            "beginner": 1,
            "intermediate": 0,
            "proficient": 0,
            "mastered": 1,
        }
        activity = data["recent_activity"]
        assert len(activity) == 7 and activity[-1]["date"] == NOW.date().isoformat()
        assert activity[-1] == {
            "date": NOW.date().isoformat(),
            "sessions": 1,
            "minutes": 10.0,
            "questions": 2,
            "concepts_practiced": 2,
        }
        assert activity[-2]["minutes"] == 20.0 and activity[0]["sessions"] == 0
        assert data["misconceptions"] == {"active": 1, "resolved": 1}
        assert (await client.get(f"{API}/mastery/overview?days=3", headers=H)).status_code == 400

    async def test_days_follow_the_students_timezone(
        self, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        await db.execute(
            update(StudentProfile)
            .where(StudentProfile.user_id == world["user"])
            .values(timezone="Pacific/Kiritimati")
        )
        await db.commit()
        data = await get(client, "/mastery/overview?days=7")
        assert (
            data["timezone"] == "Pacific/Kiritimati"
        )  # UTC+14: "today" is usually tomorrow in UTC
        local_today = datetime.now(UTC).astimezone(ZoneInfo("Pacific/Kiritimati")).date()
        assert data["recent_activity"][-1]["date"] == local_today.isoformat()

    async def test_cache_and_invalidation(
        self, app: FastAPI, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        first = await get(client, "/mastery/overview")
        db.add(
            LearningSession(
                user_id=world["user"],
                session_type="mixed",
                status="completed",
                started_at=NOW,
                duration_seconds=60,
                interaction_count=1,
            )
        )
        await db.commit()
        assert (await get(client, "/mastery/overview"))["overall_stats"] == first[
            "overall_stats"
        ]  # cached
        await invalidate_dashboard(app.state.redis, world["user"])
        assert (await get(client, "/mastery/overview"))["overall_stats"]["total_sessions"] == 3
        await invalidate_dashboard(None, world["user"])  # no Redis: no-op

    async def test_new_user_and_isolation(
        self, client: httpx.AsyncClient, world: dict[str, Any]
    ) -> None:
        other = auth("dashboard-other")
        data = await get(client, "/mastery/overview", other)
        assert data["subjects"] == [] and data["overall_stats"]["total_concepts"] == 0
        assert data["overall_stats"]["accuracy"] == 0.0 and data["misconceptions"] == {}
        assert (await get(client, "/misconceptions", other)) == []
        assert (await get(client, "/ai/usage", other))["total_interactions"] == 0
        resp = await client.get(
            f"{API}/mastery/heatmap?subject_id={world['maths'].id}", headers=other
        )
        assert resp.status_code == 404


class TestHeatmap:
    async def test_weekly_cells_from_history(
        self, client: httpx.AsyncClient, world: dict[str, Any]
    ) -> None:
        data = await get(client, "/mastery/heatmap?weeks=4")
        assert len(data["columns"]) == 4 and data["columns"][-1] == NOW.date().isoformat()
        rows = {c["name"]: c for c in data["concepts"]}
        assert [c["name"] for c in data["concepts"]][:2] == ["Vectors", "Limits"]  # recent first
        limits = rows["Limits"]
        # 3 weeks ago: before any assessment; 2 weeks ago: 0.3; last week & now: 0.85.
        assert limits["cells"] == [None, 0.3, 0.85, 0.85]
        assert limits["label"] == "mastered"
        assert rows["Loose idea"]["cells"] == [None, None, None, None]
        assert data["total_concepts"] == 3 and data["truncated"] is False

        maths = await get(client, f"/mastery/heatmap?subject_id={world['maths'].id}&limit=1")
        assert (
            len(maths["concepts"]) == 1
            and maths["truncated"] is True
            and maths["total_concepts"] == 2
        )


class TestMisconceptions:
    async def test_list_filters_and_evidence(
        self, client: httpx.AsyncClient, world: dict[str, Any]
    ) -> None:
        items = await get(client, "/misconceptions")
        assert [i["status"] for i in items] == ["active", "resolved"]  # open first
        active = items[0]
        assert active["misconception"]["name"] == "Order confusion"
        assert active["misconception"]["concept_name"] == "Vectors"
        assert [e["description"] for e in active["evidence"]] == ["latest", "first"]
        assert active["occurrence_count"] == 3
        assert [i["status"] for i in await get(client, "/misconceptions?status=resolved")] == [
            "resolved"
        ]
        by_concept = await get(client, f"/misconceptions?concept_id={world['limits'].id}")
        assert [i["misconception"]["name"] for i in by_concept] == ["Sign error"]
        assert (
            await client.get(f"{API}/misconceptions?status=weird", headers=H)
        ).status_code == 400


class TestAIUsage:
    async def test_periods_and_breakdowns(
        self, client: httpx.AsyncClient, world: dict[str, Any]
    ) -> None:
        today = await get(client, "/ai/usage")
        assert today["period"] == "today" and today["total_interactions"] == 3
        assert today["total_cost_usd"] == pytest.approx(0.13)
        assert today["today_cost_usd"] == pytest.approx(0.13)
        assert today["total_tokens"] == 450 and today["failed_interactions"] == 1
        assert today["daily_budget_usd"] == 5.0
        assert today["by_purpose"]["generate_question"] == {
            "cost": 0.03,
            "count": 2,
            "tokens": 300,
            "failed": 1,
        }
        assert set(today["by_model"]) == {"light", "heavy"}
        week = await get(client, "/ai/usage?period=week")
        assert week["total_interactions"] == 4 and len(week["daily"]) == 2
        month = await get(client, "/ai/usage?period=month")
        assert month["total_cost_usd"] == pytest.approx(1.63)
        assert next(iter(month["by_purpose"])) in ("concept_extraction", "generate_question")
        assert (await client.get(f"{API}/ai/usage?period=year", headers=H)).status_code == 400


async def test_dashboard_stays_fast_with_realistic_data(
    app: FastAPI, client: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Performance benchmark (AC-7.3 budget): 300 concepts, 3 000 answers, 120 sessions."""
    user = await provision(client, H)
    subjects = [Subject(user_id=user, name=f"Subject {i}") for i in range(6)]
    db.add_all(subjects)
    await db.flush()
    concepts = [
        Concept(user_id=user, name=f"Concept {i}", subject_id=subjects[i % 6].id)
        for i in range(300)
    ]
    db.add_all(concepts)
    await db.flush()
    db.add_all(
        StudentConceptMastery(
            user_id=user,
            concept_id=c.id,
            mastery_level=(i % 10) / 10,
            attempt_count=10,
            last_assessed_at=NOW - timedelta(days=i % 40),
            history=[
                {
                    "date": (NOW - timedelta(days=d)).date().isoformat(),
                    "mastery": 0.5,
                    "event": "practice",
                }
                for d in range(0, 60, 6)
            ],
        )
        for i, c in enumerate(concepts)
    )
    questions = [
        Question(
            user_id=user,
            concept_id=c.id,
            question_type="mcq",
            content="?",
            correct_answer={"label": "A"},
        )
        for c in concepts[:100]
    ]
    db.add_all(questions)
    await db.flush()
    db.add_all(
        QuestionAttempt(
            question_id=questions[i % 100].id,
            user_id=user,
            student_response="A",
            is_correct=i % 3 != 0,
            score=1.0,
            attempted_at=NOW - timedelta(hours=i),
        )
        for i in range(3000)
    )
    db.add_all(
        LearningSession(
            user_id=user,
            session_type="mixed",
            status="completed",
            interaction_count=5,
            started_at=NOW - timedelta(hours=12 * i),
            duration_seconds=900,
        )
        for i in range(120)
    )
    await db.commit()

    timings = {}
    for path in (
        "/mastery/overview?days=90",
        "/mastery/heatmap?weeks=12&limit=300",
        "/misconceptions",
        "/ai/usage?period=month",
    ):
        await invalidate_dashboard(app.state.redis, user)
        start = time.perf_counter()
        await get(client, path)
        timings[path] = time.perf_counter() - start
    assert max(timings.values()) < 1.5, timings  # uncached, generous for CI machines
    start = time.perf_counter()
    await get(client, "/mastery/overview?days=90")
    assert time.perf_counter() - start < 0.5  # cached
    data = await get(client, "/mastery/overview?days=90")
    assert (
        data["overall_stats"]["total_concepts"] == 300
        and data["overall_stats"]["questions_answered"] == 3000
    )
