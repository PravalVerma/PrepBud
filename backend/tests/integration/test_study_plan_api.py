"""Goals, study plans and reviews (API_CONTRACT §3.7, §3.10; AC-6.1 – AC-6.6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Concept,
    ConceptRelationship,
    ReviewItem,
    StudentConceptMastery,
    StudyPlan,
    Subject,
)
from app.services.content.document_processor import DocumentProcessor
from app.services.study_plan.plan_service import StudyPlanService
from app.workers.maintenance_tasks import learner_ids, refresh_learner
from tests.fakes import FakeLLMProvider, FakeQueue, concept_text
from tests.integration.conftest import Uploader, drain, provision
from tests.support import auth

API = "/api/v1"
H = auth("planner")
OTHER = auth("someone-else")
TODAY = datetime.now(UTC).date()


@pytest.fixture
async def world(
    client: httpx.AsyncClient, db: AsyncSession, fake_llm: FakeLLMProvider
) -> dict[str, Any]:
    """Limits → Derivatives → Chain Rule (prerequisites) in Calculus, plus Vectors."""
    fake_llm.stream_text = "Here is a clear explanation of the idea."
    user_id = await provision(client, H)
    calculus = Subject(user_id=user_id, name="Calculus")
    db.add(calculus)
    await db.flush()
    names = {"Limits": 0.3, "Derivatives": 0.5, "Chain Rule": 0.7, "Vectors": 0.4}
    concepts = {
        n: Concept(
            user_id=user_id,
            name=n,
            difficulty_estimate=d,
            subject_id=calculus.id if n != "Vectors" else None,
        )
        for n, d in names.items()
    }
    db.add_all(concepts.values())
    await db.flush()
    for s, t in [("Limits", "Derivatives"), ("Derivatives", "Chain Rule")]:
        db.add(
            ConceptRelationship(
                source_concept_id=concepts[s].id,
                target_concept_id=concepts[t].id,
                relationship_type="prerequisite",
            )
        )
    await db.commit()
    return {"user_id": user_id, "subject": calculus, **concepts}


async def mastery(
    db: AsyncSession, user_id: uuid.UUID, concept: Concept, **values: Any
) -> StudentConceptMastery:
    row = StudentConceptMastery(user_id=user_id, concept_id=concept.id, **values)
    db.add(row)
    await db.commit()
    return row


async def plan(client: httpx.AsyncClient, headers: dict[str, str] = H) -> dict[str, Any]:
    resp = await client.get(f"{API}/study-plan", headers=headers)
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


async def create_goal(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post(f"{API}/goals", json=body, headers=H)
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


class TestGoals:
    async def test_creating_a_goal_generates_a_plan(
        self, client: httpx.AsyncClient, world: dict[str, Any]
    ) -> None:
        """AC-6.3 — prerequisites first, spread to the target date."""
        ids = [str(world[n].id) for n in ("Chain Rule", "Derivatives", "Limits")]
        goal = await create_goal(
            client,
            {
                "title": "Master differentiation",
                "goal_type": "deadline",
                "target_date": (TODAY + timedelta(days=1)).isoformat(),
                "target_concept_ids": ids,
            },
        )
        assert goal["status"] == "active" and goal["target_concept_ids"] == ids
        assert goal["study_plan"]["total_items"] == 3 and goal["study_plan"]["due_today"] == 2
        assert goal["progress"] == {
            "concept_count": 3,
            "mastered_count": 0,
            "average_mastery": 0.0,
            "progress": 0.0,
            "days_remaining": 1,
            "all_mastered": False,
        }
        assert [c["name"] for c in goal["concepts"]] == ["Chain Rule", "Derivatives", "Limits"]

        data = await plan(client)
        assert data["id"] == goal["study_plan"]["id"]
        assert data["learning_goal_id"] == goal["id"]
        names = [i["concept_name"] for i in data["items"]]
        assert names == ["Limits", "Derivatives", "Chain Rule"]
        assert [i["scheduled_date"] for i in data["items"]] == [
            TODAY.isoformat(),
            TODAY.isoformat(),
            (TODAY + timedelta(days=1)).isoformat(),
        ]
        assert {i["kind"] for i in data["items"]} == {"learn"}
        assert all(i["goal_id"] == goal["id"] for i in data["items"])
        assert data["stats"]["upcoming_today"] == 2 and data["stats"]["overdue"] == 0
        assert data["stats"]["next_due_date"] == (TODAY + timedelta(days=1)).isoformat()

    async def test_scope_by_subject_and_everything(
        self, client: httpx.AsyncClient, world: dict[str, Any]
    ) -> None:
        by_subject = await create_goal(
            client, {"title": "Calculus", "subject_id": str(world["subject"].id)}
        )
        assert by_subject["progress"]["concept_count"] == 3
        everything = await create_goal(client, {"title": "Everything"})
        assert everything["progress"]["concept_count"] == 4
        data = await plan(client)
        assert data["learning_goal_id"] is None  # several active goals
        assert len(data["items"]) == 4  # one item per concept across goals

    async def test_validation_and_isolation(
        self, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        bad = await client.post(
            f"{API}/goals", json={"title": "x", "goal_type": "deadline"}, headers=H
        )
        assert bad.status_code == 400
        assert (await client.post(f"{API}/goals", json={"title": ""}, headers=H)).status_code == 400
        other_user = await provision(client, OTHER)
        foreign = Concept(user_id=other_user, name="Theirs")
        foreign_subject = Subject(user_id=other_user, name="Theirs")
        db.add_all([foreign, foreign_subject])
        await db.commit()
        for body in (
            {"title": "x", "target_concept_ids": [str(foreign.id)]},
            {"title": "x", "subject_id": str(foreign_subject.id)},
            {"title": "x", "course_id": str(uuid.uuid4())},
        ):
            assert (await client.post(f"{API}/goals", json=body, headers=H)).status_code == 404

        goal = await create_goal(client, {"title": "Mine"})
        for method in ("GET", "PATCH", "DELETE"):
            body = {"title": "stolen"} if method == "PATCH" else None
            resp = await client.request(
                method, f"{API}/goals/{goal['id']}", json=body, headers=OTHER
            )
            assert resp.status_code == 404
        assert (await client.get(f"{API}/goals", headers=OTHER)).json()["data"] == []
        item = (await plan(client))["items"][0]
        resp = await client.patch(
            f"{API}/study-plan/items/{item['id']}", json={"status": "skipped"}, headers=OTHER
        )
        assert resp.status_code == 404

    async def test_update_list_progress_and_delete(
        self, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        goal = await create_goal(
            client, {"title": "Limits & co", "target_concept_ids": [str(world["Limits"].id)]}
        )
        await mastery(
            db,
            world["user_id"],
            world["Limits"],
            mastery_level=0.9,
            attempt_count=5,
            last_assessed_at=datetime.now(UTC),
        )
        listed = (await client.get(f"{API}/goals", headers=H)).json()
        assert listed["meta"]["pagination"]["total"] == 1
        assert listed["data"][0]["progress"]["mastered_count"] == 1
        assert listed["data"][0]["progress"]["all_mastered"] is True
        assert "concepts" not in listed["data"][0] or listed["data"][0]["concepts"] is None

        patched = await client.patch(
            f"{API}/goals/{goal['id']}",
            json={"status": "paused", "title": "Paused goal"},
            headers=H,
        )
        assert patched.status_code == 200 and patched.json()["data"]["status"] == "paused"
        assert (await client.get(f"{API}/goals?status=active", headers=H)).json()["data"] == []
        assert (await client.get(f"{API}/goals?status=bogus", headers=H)).status_code == 400
        assert (
            await client.patch(f"{API}/goals/{goal['id']}", json={"status": None}, headers=H)
        ).status_code == 400
        assert (
            await client.patch(
                f"{API}/goals/{goal['id']}", json={"goal_type": "deadline"}, headers=H
            )
        ).status_code == 400
        changed = await client.patch(
            f"{API}/goals/{goal['id']}",
            json={"status": "active", "target_concept_ids": [str(world["Vectors"].id)]},
            headers=H,
        )
        assert [c["name"] for c in changed.json()["data"]["concepts"]] == ["Vectors"]
        assert [i["concept_name"] for i in (await plan(client))["items"]] == ["Vectors"]

        assert (await client.delete(f"{API}/goals/{goal['id']}", headers=H)).status_code == 204
        assert (await client.get(f"{API}/goals/{goal['id']}", headers=H)).status_code == 404
        assert (await plan(client))["items"] == []


class TestPlan:
    async def test_overdue_reviews_come_first(
        self, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        """AC-6.2."""
        now = datetime.now(UTC)
        await create_goal(
            client, {"title": "Vectors", "target_concept_ids": [str(world["Vectors"].id)]}
        )
        await mastery(
            db,
            world["user_id"],
            world["Limits"],
            mastery_level=0.75,
            attempt_count=3,
            last_assessed_at=now,
            next_review_at=now - timedelta(days=3),
        )
        await mastery(
            db,
            world["user_id"],
            world["Derivatives"],
            mastery_level=0.75,
            attempt_count=3,
            last_assessed_at=now,
            next_review_at=now + timedelta(days=2),
        )
        regenerated = await client.post(f"{API}/study-plan/regenerate", headers=H)
        data = regenerated.json()["data"]
        first, *rest = data["items"]
        assert first["concept_name"] == "Limits"
        assert first["status"] == "overdue" and first["kind"] == "review"
        assert first["scheduled_date"] == (TODAY - timedelta(days=3)).isoformat()
        assert [i["concept_name"] for i in rest] == ["Vectors", "Derivatives"]
        assert data["stats"]["overdue"] == 1 and data["stats"]["reviews_due"] == 1
        assert data["stats"]["due_today"] == 2

    async def test_item_updates_and_carry_over(
        self, client: httpx.AsyncClient, world: dict[str, Any]
    ) -> None:
        await create_goal(client, {"title": "Calculus", "subject_id": str(world["subject"].id)})
        items = (await plan(client))["items"]
        limits, derivatives = items[0], items[1]
        url = f"{API}/study-plan/items/{limits['id']}"

        skipped = await client.patch(url, json={"status": "skipped"}, headers=H)
        assert skipped.status_code == 200 and skipped.json()["data"]["status"] == "skipped"
        back = await client.patch(url, json={"status": "pending"}, headers=H)
        assert back.json()["data"]["status"] == "pending"
        tomorrow = (TODAY + timedelta(days=1)).isoformat()
        moved = await client.patch(url, json={"scheduled_date": tomorrow}, headers=H)
        assert moved.json()["data"]["scheduled_date"] == tomorrow
        past = (TODAY - timedelta(days=1)).isoformat()
        assert (
            await client.patch(url, json={"scheduled_date": past}, headers=H)
        ).status_code == 400
        assert (await client.patch(url, json={}, headers=H)).status_code == 400
        assert (
            await client.patch(
                f"{API}/study-plan/items/{uuid.uuid4()}", json={"status": "skipped"}, headers=H
            )
        ).status_code == 404

        done = await client.patch(
            f"{API}/study-plan/items/{derivatives['id']}", json={"status": "completed"}, headers=H
        )
        assert done.json()["data"]["completed_at"]
        # Regenerating keeps today's completed item and moves that concept to tomorrow.
        data = (await client.post(f"{API}/study-plan/regenerate", headers=H)).json()["data"]
        statuses = [(i["concept_name"], i["status"], i["scheduled_date"]) for i in data["items"]]
        assert ("Derivatives", "completed", TODAY.isoformat()) in statuses
        assert all(
            d > TODAY.isoformat() for n, s, d in statuses if n == "Derivatives" and s != "completed"
        )
        assert data["stats"]["completed"] == 1
        assert data["items"][-1]["status"] == "completed"  # done items last
        # The old item belongs to a superseded plan now.
        gone = await client.patch(
            f"{API}/study-plan/items/{derivatives['id']}", json={"status": "skipped"}, headers=H
        )
        assert gone.status_code == 404

    async def test_plan_is_created_lazily_and_refreshed_daily(
        self, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        first = await plan(client)
        assert first["items"] == [] and first["stats"]["total_items"] == 0
        assert (await plan(client))["id"] == first["id"]  # same day: same plan
        row = await db.get(StudyPlan, uuid.UUID(first["id"]))
        assert row is not None
        row.generation_metadata = {**(row.generation_metadata or {}), "generated_for": "2000-01-01"}
        await db.commit()
        assert (await plan(client))["id"] != first["id"]
        statuses = (
            await db.scalars(select(StudyPlan.status).order_by(StudyPlan.generated_at))
        ).all()
        assert list(statuses) == ["superseded", "active"]


class TestSessionsAndReviews:
    async def test_session_completion_ticks_off_items_and_schedules_reviews(
        self, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        """AC-6.1 + plan regeneration on session end + review item status updates."""
        await create_goal(
            client, {"title": "Limits", "target_concept_ids": [str(world["Limits"].id)]}
        )
        before = await plan(client)
        created = await client.post(
            f"{API}/sessions",
            json={"session_type": "practice", "concept_ids": [str(world["Limits"].id)]},
            headers=H,
        )
        sid = created.json()["data"]["session_id"]
        turn = (
            await client.post(f"{API}/sessions/{sid}/messages", json={"type": "begin"}, headers=H)
        ).json()["data"]
        question = turn["current_question"]
        answer = await client.post(
            f"{API}/sessions/{sid}/messages",
            json={
                "type": "student_response",
                "payload": {"content": "true", "question_id": question["question_id"]},
            },
            headers=H,
        )
        assert answer.status_code == 200, answer.text
        ended = await client.post(f"{API}/sessions/{sid}/end", headers=H)
        assert ended.json()["data"]["status"] == "completed"

        row = await db.scalar(
            select(StudentConceptMastery).where(
                StudentConceptMastery.concept_id == world["Limits"].id
            )
        )
        assert row is not None and row.next_review_at is not None  # AC-6.1
        assert row.next_review_at.date() > TODAY

        after = await plan(client)
        assert after["id"] != before["id"]
        done = [i for i in after["items"] if i["status"] == "completed"]
        assert [i["concept_name"] for i in done] == ["Limits"]
        assert after["stats"]["completed"] == 1
        upcoming = [i for i in after["items"] if i["status"] == "pending"]
        assert upcoming and upcoming[0]["scheduled_date"] > TODAY.isoformat()
        assert upcoming[0]["next_review_at"]

    async def test_review_session_targets_due_items(
        self, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        """AC-6.6."""
        now = datetime.now(UTC)
        user = world["user_id"]
        await mastery(
            db,
            user,
            world["Derivatives"],
            mastery_level=0.8,
            attempt_count=4,
            last_assessed_at=now,
            next_review_at=now - timedelta(days=1),
        )
        await mastery(
            db,
            user,
            world["Vectors"],
            mastery_level=0.6,
            attempt_count=4,
            last_assessed_at=now,
            next_review_at=now - timedelta(days=4),
        )
        await mastery(
            db,
            user,
            world["Limits"],
            mastery_level=0.9,
            attempt_count=4,
            last_assessed_at=now,
            next_review_at=now + timedelta(days=9),
        )
        data = await plan(client)
        assert [i["concept_name"] for i in data["items"]][:2] == ["Vectors", "Derivatives"]

        created = await client.post(f"{API}/sessions", json={"session_type": "review"}, headers=H)
        assert created.status_code == 201, created.text
        targets = created.json()["data"]["objective"]["target_concepts"]
        assert [t["name"] for t in targets] == ["Vectors", "Derivatives"]
        assert {t["reason"] for t in targets} == {"overdue_review"}

    async def test_learn_items_feed_study_sessions_not_reviews(
        self, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        await create_goal(
            client, {"title": "Vectors", "target_concept_ids": [str(world["Vectors"].id)]}
        )
        await mastery(
            db,
            world["user_id"],
            world["Derivatives"],
            mastery_level=0.8,
            attempt_count=4,
            last_assessed_at=now,
            next_review_at=now - timedelta(days=1),
        )
        await client.post(f"{API}/study-plan/regenerate", headers=H)

        review = await client.post(f"{API}/sessions", json={"session_type": "review"}, headers=H)
        assert [t["name"] for t in review.json()["data"]["objective"]["target_concepts"]] == [
            "Derivatives"
        ]
        teach = await client.post(f"{API}/sessions", json={"session_type": "teach"}, headers=H)
        first = teach.json()["data"]["objective"]["target_concepts"][0]
        assert first["name"] == "Vectors" and first["reason"] == "goal"


class TestMaintenance:
    async def test_daily_refresh_decays_marks_overdue_and_replans(
        self, app: FastAPI, client: httpx.AsyncClient, world: dict[str, Any], db: AsyncSession
    ) -> None:
        """AC-6.5 (decay is visible everywhere, never compounds) + overdue marking."""
        user = world["user_id"]
        long_ago = datetime.now(UTC) - timedelta(days=20)
        await mastery(
            db,
            user,
            world["Limits"],
            mastery_level=0.9,
            attempt_count=6,
            last_assessed_at=long_ago,
            ease_factor=2.5,
            next_review_at=None,
            history=[{"date": long_ago.date().isoformat(), "mastery": 0.9, "event": "practice"}],
        )
        await create_goal(
            client, {"title": "Vectors", "target_concept_ids": [str(world["Vectors"].id)]}
        )
        stale = await db.scalar(select(ReviewItem))
        assert stale is not None
        stale.scheduled_date = TODAY - timedelta(days=1)
        await db.commit()

        assert user in await learner_ids(db)
        settings = app.state.settings
        result = await refresh_learner(app.state.sessionmaker, app.state.redis, settings, user)
        assert result["decayed"] == 1 and result["overdue"] == 1

        detail = (await client.get(f"{API}/concepts/{world['Limits'].id}", headers=H)).json()[
            "data"
        ]
        level = detail["mastery"]["level"]
        assert 0.35 < level < 0.45  # 0.9·e^(−20/25) ≈ 0.40, read straight from the stored row
        assert detail["mastery"]["history"][-1]["event"] == "decay"

        again = await refresh_learner(app.state.sessionmaker, app.state.redis, settings, user)
        assert again["decayed"] == 0 or again["decayed"] == 1  # a few ms of extra decay at most
        detail = (await client.get(f"{API}/concepts/{world['Limits'].id}", headers=H)).json()[
            "data"
        ]
        assert detail["mastery"]["level"] == pytest.approx(level, abs=0.001)  # no compounding

        data = await plan(client)
        forgotten = next(i for i in data["items"] if i["concept_name"] == "Limits")
        assert forgotten["kind"] == "review" and forgotten["scheduled_date"] == TODAY.isoformat()
        plans = (await db.scalars(select(StudyPlan.generation_metadata))).all()
        assert any((m or {}).get("reason") == "daily" for m in plans)

    async def test_review_intervals_grow(
        self, app: FastAPI, world: dict[str, Any], db: AsyncSession
    ) -> None:
        """AC-6.4 through the tracker: consecutive successful reviews stretch the interval."""
        service = StudyPlanService(db, app.state.settings, redis=app.state.redis)
        intervals = []
        for _ in range(4):
            schedule = await service.tracker.schedule_review(
                world["user_id"], world["Limits"].id, 1.0
            )
            await db.commit()
            intervals.append(schedule.next_interval_days)
        assert intervals[0] == 1.0 and intervals[1] == 6.0
        assert intervals[2] > intervals[1] and intervals[3] > intervals[2]
        failed = await service.tracker.schedule_review(world["user_id"], world["Limits"].id, 0.2)
        assert failed.next_interval_days == 1.0  # a failed review starts over


async def test_new_document_concepts_join_the_plan(
    client: httpx.AsyncClient,
    uploader: Uploader,
    processor: DocumentProcessor,
    fake_queue: FakeQueue,
) -> None:
    """LEARNING_ENGINE §8.3: a processed document adds its concepts to active goals' plans."""
    await provision(client, H)
    goal = await create_goal(client, {"title": "Everything I upload"})
    assert goal["study_plan"]["total_items"] == 0
    await uploader.upload(H, concept_text("Momentum", "Mass times velocity").encode())
    assert await drain(processor, fake_queue) == ["ready"]
    data = await plan(client)
    assert [i["concept_name"] for i in data["items"]] == ["Momentum"]
    assert data["items"][0]["goal_id"] == goal["id"]
