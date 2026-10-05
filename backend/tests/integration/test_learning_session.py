"""Learning sessions end to end through `SessionManager` — real PostgreSQL + Redis, fake LLM.

Covers AC-4.1 (mastery direction), AC-4.3 (difficulty adapts), AC-4.4 (state machine),
AC-4.5 (recovery after interruption), AC-4.6 (misconceptions stored), AC-4.8
(frustration guard), plus error handling and isolation.
"""

from __future__ import annotations

import itertools
import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import LLMResponseError, LLMServerError
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from app.db.models import (
    AITrace,
    Concept,
    ConceptRelationship,
    LearningGoal,
    LearningSession,
    QuestionAttempt,
    SessionEvent,
    StudentConceptMastery,
    StudentMisconception,
    StudentProfile,
)
from app.services.learning_engine.session_manager import SessionManager, TurnResult, state_key
from app.services.learning_engine.state import VALID_TRANSITIONS, ClientMessage, StartSessionRequest
from tests.fakes import FakeLLMProvider
from tests.integration.conftest import provision
from tests.support import auth

H = auth("learner")


@pytest.fixture
async def concepts(client: httpx.AsyncClient, db: AsyncSession) -> dict[str, Concept]:
    """Limits → Derivatives (prerequisite), and an unrelated Vectors."""
    user_id = await provision(client, H)
    items = {
        "Limits": Concept(
            user_id=user_id,
            name="Limits",
            description="Values a function approaches",
            difficulty_estimate=0.3,
        ),
        "Derivatives": Concept(
            user_id=user_id,
            name="Derivatives",
            description="Rate of change",
            difficulty_estimate=0.5,
        ),
        "Vectors": Concept(
            user_id=user_id,
            name="Vectors",
            description="Magnitude and direction",
            difficulty_estimate=0.6,
        ),
    }
    db.add_all(items.values())
    await db.flush()
    db.add(
        ConceptRelationship(
            source_concept_id=items["Limits"].id,
            target_concept_id=items["Derivatives"].id,
            relationship_type="prerequisite",
        )
    )
    await db.commit()
    return items


def types(result: TurnResult) -> list[str]:
    return [e["type"] for e in result.events]


def event(result: TurnResult, kind: str) -> dict[str, Any]:
    return next(e["payload"] for e in result.events if e["type"] == kind)


def correct_answer(question: dict[str, Any]) -> str:
    return {"true_false": "true", "mcq": "B"}.get(question["type"], "correct-answer")


def wrong_answer(question: dict[str, Any]) -> str:
    return {"true_false": "false", "mcq": "C"}.get(question["type"], "no idea")


async def answer(
    sm: SessionManager, user_id: uuid.UUID, result: TurnResult, text: str, **extra: Any
) -> TurnResult:
    q = result.current_question
    assert q is not None, f"no open question (awaiting={result.awaiting})"
    payload = {"content": text, "question_id": q["question_id"], **extra}
    return await sm.handle(
        user_id, result.session_id, ClientMessage(type="student_response", payload=payload)
    )


async def ack(
    sm: SessionManager, user_id: uuid.UUID, result: TurnResult, understood: bool = True
) -> TurnResult:
    return await sm.handle(
        user_id,
        result.session_id,
        ClientMessage(type="student_acknowledge", payload={"understood": understood}),
    )


async def until_question(sm: SessionManager, user_id: uuid.UUID, result: TurnResult) -> TurnResult:
    while result.awaiting == "acknowledgement":
        result = await ack(sm, user_id, result)
    return result


async def state_of(app: FastAPI, session_id: uuid.UUID) -> dict[str, Any]:
    return json.loads(await app.state.redis.get(state_key(session_id)))


class TestStart:
    async def test_plans_and_explains_first_concept(
        self,
        app: FastAPI,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
        db: AsyncSession,
    ) -> None:
        user_id = concepts["Limits"].user_id
        created = await session_manager.create(user_id, StartSessionRequest(session_type="mixed"))
        # Creating only plans (no AI calls): objective known, nothing taught yet.
        assert created.status == "initialising" and created.awaiting == "none"
        assert types(created) == ["session_started"]
        result = await session_manager.begin(user_id, created.session_id)

        assert result.status == "active" and result.awaiting == "acknowledgement"
        assert types(result) == ["explanation_start", "explanation_chunk", "explanation_end"]
        targets = [t["name"] for t in result.objective["target_concepts"]]
        # Derivatives is blocked by its prerequisite; weakest teachable first, easier first on ties.
        assert targets == ["Limits", "Vectors"]
        assert result.objective["target_concepts"][0]["action"] == "teach"
        assert (
            event(result, "explanation_chunk")["content"]
            == "Here is a clear explanation of the idea."
        )
        assert event(result, "explanation_start")["kind"] == "intro"

        row = await db.scalar(
            select(LearningSession).where(LearningSession.id == result.session_id)
        )
        assert row is not None and row.status == "active" and row.session_type == "mixed"
        assert (row.metadata_ or {})["checkpoint"]["awaiting"] == "acknowledgement"
        assert row.objective["session_type"] == "mixed"
        logged = (
            await db.scalars(select(SessionEvent.event_type).order_by(SessionEvent.event_index))
        ).all()
        assert logged == ["concept_changed", "explanation_given"]
        traces = (
            await db.scalars(
                select(AITrace.operation).where(
                    AITrace.session_id == result.session_id, AITrace.status == "completed"
                )
            )
        ).all()
        assert sorted(traces) == ["session_begin", "session_plan"]
        assert (await state_of(app, result.session_id))["turn"] == 1
        # begin on a running session only re-sends the view
        again = await session_manager.begin(user_id, created.session_id)
        assert types(again) == ["explanation_start", "explanation_chunk", "explanation_end"]
        assert (await state_of(app, result.session_id))["turn"] == 1

    async def test_explicit_concepts_start_with_their_prerequisites(
        self, session_manager: SessionManager, concepts: dict[str, Concept]
    ) -> None:
        req = StartSessionRequest(concept_ids=[concepts["Derivatives"].id])
        result = await session_manager.start(concepts["Limits"].user_id, req)
        names = [t["name"] for t in result.objective["target_concepts"]]
        assert names == ["Limits", "Derivatives"]
        assert [t["reason"] for t in result.objective["target_concepts"]] == [
            "prerequisite",
            "requested",
        ]

    async def test_goal_concepts_and_practice_sessions(
        self, session_manager: SessionManager, concepts: dict[str, Concept], db: AsyncSession
    ) -> None:
        user_id = concepts["Limits"].user_id
        goal = LearningGoal(
            user_id=user_id, title="Vectors", target_concepts=[concepts["Vectors"].id]
        )
        db.add(goal)
        await db.commit()
        result = await session_manager.start(
            user_id, StartSessionRequest(session_type="practice", learning_goal_id=goal.id)
        )
        assert result.objective["target_concepts"][0]["name"] == "Vectors"
        assert (
            result.awaiting == "answer" and result.current_question is not None
        )  # practice starts with a question
        assert "hints" not in result.current_question

    async def test_no_concepts_is_422_and_leaves_nothing_behind(
        self, client: httpx.AsyncClient, session_manager: SessionManager, db: AsyncSession
    ) -> None:
        user_id = await provision(client, auth("empty-learner"))
        with pytest.raises(UnprocessableEntityError, match="upload"):
            await session_manager.start(user_id, StartSessionRequest())
        assert await db.scalar(select(func.count()).select_from(LearningSession)) == 0

    async def test_review_session_with_nothing_learned(
        self, session_manager: SessionManager, concepts: dict[str, Concept]
    ) -> None:
        with pytest.raises(UnprocessableEntityError, match="review"):
            await session_manager.start(
                concepts["Limits"].user_id, StartSessionRequest(session_type="review")
            )

    async def test_other_users_goal_or_concepts_are_404(
        self,
        client: httpx.AsyncClient,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
    ) -> None:
        stranger = await provision(client, auth("stranger"))
        with pytest.raises(NotFoundError):
            await session_manager.start(
                stranger, StartSessionRequest(concept_ids=[concepts["Limits"].id])
            )
        with pytest.raises(NotFoundError):
            await session_manager.start(
                stranger, StartSessionRequest(learning_goal_id=uuid.uuid4())
            )

    async def test_llm_outage_at_start_is_503(
        self,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        fake_llm.stream_fail_with = LLMServerError("down")
        with pytest.raises(ServiceUnavailableError):
            await session_manager.start(concepts["Limits"].user_id, StartSessionRequest())
        assert await db.scalar(select(func.count()).select_from(LearningSession)) == 0


class TestFlow:
    async def test_full_session_to_summary(
        self,
        app: FastAPI,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
        db: AsyncSession,
    ) -> None:
        user_id = concepts["Limits"].user_id
        sm = session_manager
        result = await sm.start(user_id, StartSessionRequest(session_type="mixed"))
        result = await until_question(sm, user_id, result)
        assert (
            result.current_question and result.current_question["type"] == "true_false"
        )  # novice → recognition

        levels, seen_concepts = [], set()
        for _ in range(12):  # answer correctly until the engine moves on and finishes
            if result.awaiting == "ended":
                break
            result = await until_question(sm, user_id, result)
            if result.awaiting == "ended":
                break
            seen_concepts.add(result.current_concept_id)
            result = await answer(
                sm, user_id, result, correct_answer(result.current_question), time_taken_seconds=12
            )
            evaluation = event(result, "evaluation")
            assert evaluation["is_correct"] and evaluation["score"] == 1.0
            update = evaluation["mastery_update"]
            assert update["new_mastery"] > update["old_mastery"]  # AC-4.1
            levels.append(update["new_mastery"])
        else:
            result = await sm.end(user_id, result.session_id)

        assert seen_concepts == {str(concepts["Limits"].id), str(concepts["Vectors"].id)}
        assert all(0.0 <= level <= 1.0 for level in levels)  # AC-4.2
        assert result.status == "completed" and result.awaiting == "ended"
        summary = event(result, "session_ended")["summary"]
        assert summary["questions_answered"] == len(levels) and summary["accuracy"] == 1.0
        assert summary["text"] == "You made solid progress today. Keep going!"
        assert {c["concept"] for c in summary["mastery_changes"]} == {"Limits", "Vectors"}
        assert all(c["to"] > c["from"] for c in summary["mastery_changes"])
        assert len(summary["reviews"]) == 2  # SM-2 scheduled per practised concept

        row = await db.scalar(
            select(LearningSession).where(LearningSession.id == result.session_id)
        )
        assert row is not None and row.status == "completed" and row.ended_at and row.summary
        assert (
            row.duration_seconds is not None and row.interaction_count == result.interaction_count
        )
        assert set(row.concepts_covered or []) == {concepts["Limits"].id, concepts["Vectors"].id}
        mastery = (await db.scalars(select(StudentConceptMastery))).all()
        assert {m.concept_id for m in mastery} == {concepts["Limits"].id, concepts["Vectors"].id}
        assert all(m.next_review_at is not None and (m.mastery_level or 0) > 0.5 for m in mastery)
        assert await db.scalar(select(func.count()).select_from(QuestionAttempt)) == len(levels)
        profile = await db.scalar(select(StudentProfile).where(StudentProfile.user_id == user_id))
        assert profile is not None and (profile.cumulative_stats or {})["total_sessions"] == 1

        # AC-4.4: every consecutive pair of visited states is a valid transition.
        trail = (await state_of(app, result.session_id))["state_trail"]
        assert trail[0] == "INITIALISE" and trail[-2:] == ["WRAP_UP", "SCHEDULE_REVIEW"]
        for current, nxt in itertools.pairwise(trail):
            assert nxt in VALID_TRANSITIONS[current], (current, nxt)
        assert {
            "PLAN_SESSION",
            "EXPLAIN",
            "PRACTICE",
            "EVALUATE_RESPONSE",
            "UPDATE_MASTERY",
            "DECIDE_NEXT",
        } <= set(trail)

        with pytest.raises(ConflictError, match="ended"):
            await sm.handle(user_id, result.session_id, ClientMessage(type="request_hint"))

    async def test_difficulty_rises_with_mastery(
        self, session_manager: SessionManager, concepts: dict[str, Concept], db: AsyncSession
    ) -> None:
        """AC-4.3: the target difficulty follows mastery upward after correct answers."""
        user_id = concepts["Limits"].user_id
        sm = session_manager
        result = await sm.start(
            user_id,
            StartSessionRequest(session_type="practice", concept_ids=[concepts["Vectors"].id]),
        )
        for _ in range(4):
            result = await answer(sm, user_id, result, correct_answer(result.current_question))
            result = await until_question(sm, user_id, result)
        targets = [
            p["target_difficulty"]
            for p in (
                await db.scalars(
                    select(SessionEvent.payload)
                    .where(SessionEvent.event_type == "question_asked")
                    .order_by(SessionEvent.event_index)
                )
            ).all()
        ]
        assert len(targets) >= 4 and targets == sorted(targets) and targets[-1] > targets[0]

    async def test_wrong_answers_lower_mastery_and_record_misconceptions(
        self, session_manager: SessionManager, concepts: dict[str, Concept], db: AsyncSession
    ) -> None:
        user_id = concepts["Limits"].user_id
        sm = session_manager
        result = await sm.start(
            user_id,
            StartSessionRequest(session_type="practice", concept_ids=[concepts["Vectors"].id]),
        )
        # Raise mastery a little first so a wrong answer has something to lower.
        result = await answer(sm, user_id, result, correct_answer(result.current_question))
        result = await until_question(sm, user_id, result)
        assert result.current_question is not None
        if result.current_question["type"] != "mcq":
            result = await answer(sm, user_id, result, correct_answer(result.current_question))
            result = await until_question(sm, user_id, result)
        assert result.current_question is not None and result.current_question["type"] == "mcq"

        result = await answer(sm, user_id, result, "A")  # distractor tagged "confuses_terms"
        evaluation = event(result, "evaluation")
        assert not evaluation["is_correct"] and evaluation["correct_answer"] == "B) correct-answer"
        assert (
            evaluation["mastery_update"]["new_mastery"]
            < evaluation["mastery_update"]["old_mastery"]
        )  # AC-4.1
        detected = event(result, "misconception_detected")
        assert detected["misconception_name"] == "Confuses terms" and detected["status"] == "active"
        # AC-4.6: stored against the student, linked to the attempt.
        sm_row = await db.scalar(select(StudentMisconception))
        assert sm_row is not None and sm_row.status == "active" and sm_row.occurrence_count == 1
        attempt = await db.scalar(
            select(QuestionAttempt).where(QuestionAttempt.is_correct.is_(False))
        )
        assert (
            attempt is not None
            and attempt.misconceptions_detected
            and attempt.mastery_delta
            and attempt.mastery_delta < 0
        )
        assert sm_row.evidence and sm_row.evidence[0]["attempt_id"] == str(attempt.id)
        # Low score → the tutor re-explains.
        assert "explanation_start" in types(result) and result.awaiting == "acknowledgement"

    async def test_free_text_answer_misconception_via_llm(
        self, session_manager: SessionManager, concepts: dict[str, Concept], db: AsyncSession
    ) -> None:
        user_id = concepts["Limits"].user_id
        sm = session_manager
        # Mastery 0.5 → short-answer questions (graded by the LLM).
        db.add(
            StudentConceptMastery(
                user_id=user_id, concept_id=concepts["Vectors"].id, mastery_level=0.5
            )
        )
        await db.commit()
        result = await sm.start(
            user_id,
            StartSessionRequest(session_type="practice", concept_ids=[concepts["Vectors"].id]),
        )
        assert result.current_question and result.current_question["type"] == "short_answer"
        result = await answer(sm, user_id, result, "minus MISC")
        assert event(result, "misconception_detected")["misconception_name"] == "Sign error"

    async def test_frustration_guard(
        self,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
        db: AsyncSession,
        app: FastAPI,
    ) -> None:
        """AC-4.8: five consecutive failures → encouragement, easier work, lower difficulty;
        a second activation ends the session with a break suggestion."""
        user_id = concepts["Limits"].user_id
        sm = session_manager
        result = await sm.start(user_id, StartSessionRequest(session_type="practice"))
        messages: list[dict[str, Any]] = []
        for _ in range(30):
            result = await until_question(sm, user_id, result)
            if result.awaiting == "ended":
                break
            result = await answer(sm, user_id, result, wrong_answer(result.current_question))
            messages += [e["payload"] for e in result.events if e["type"] == "tutor_message"]
            if result.awaiting == "ended":
                break
        frustration = (
            await db.scalars(
                select(SessionEvent).where(SessionEvent.event_type == "frustration_detected")
            )
        ).all()
        assert len(frustration) == 2
        assert "tricky" in messages[0]["content"] and "break" in messages[-1]["content"]
        assert (
            result.status == "completed"
            and event(result, "session_ended")["summary"]["end_reason"] == "frustration"
        )
        state = await state_of(app, result.session_id)
        assert state["difficulty_offset"] == pytest.approx(-0.4)
        attempts = (
            await db.scalars(select(QuestionAttempt).order_by(QuestionAttempt.attempted_at))
        ).all()
        assert len(attempts) == 10 and not any(a.is_correct for a in attempts)

    async def test_hints_and_follow_up_questions(
        self, session_manager: SessionManager, concepts: dict[str, Concept], db: AsyncSession
    ) -> None:
        user_id = concepts["Limits"].user_id
        sm = session_manager
        result = await sm.start(user_id, StartSessionRequest(session_type="practice"))
        sid = result.session_id
        hint1 = await sm.handle(user_id, sid, ClientMessage(type="request_hint"))
        hint2 = await sm.handle(user_id, sid, ClientMessage(type="request_hint"))
        none_left = await sm.handle(user_id, sid, ClientMessage(type="request_hint"))
        assert event(hint1, "hint")["hint_number"] == 1 and event(hint2, "hint")["hint_number"] == 2
        assert event(none_left, "tutor_message")["kind"] == "no_more_hints"
        assert hint2.current_question and hint2.current_question["hints_available"] == 0

        follow = await sm.handle(
            user_id,
            sid,
            ClientMessage(type="student_question", payload={"content": "Why does this work?"}),
        )
        assert types(follow) == ["explanation_start", "explanation_chunk", "explanation_end"]
        assert event(follow, "explanation_start")["kind"] == "followup"
        assert follow.awaiting == "answer" and follow.current_question == hint2.current_question
        logged = set((await db.scalars(select(SessionEvent.event_type))).all())
        assert {"hint_given", "follow_up_question", "follow_up_answer"} <= logged

    async def test_confused_student_gets_a_different_explanation(
        self, session_manager: SessionManager, concepts: dict[str, Concept]
    ) -> None:
        user_id = concepts["Limits"].user_id
        result = await session_manager.start(user_id, StartSessionRequest(session_type="teach"))
        retry = await ack(session_manager, user_id, result, understood=False)
        assert event(retry, "explanation_start")["kind"] == "retry"
        again = await ack(session_manager, user_id, retry, understood=False)
        assert event(again, "explanation_start")["kind"] == "worked_example"
        practice = await ack(session_manager, user_id, again, understood=True)
        assert practice.awaiting == "answer"

    async def test_messages_must_match_the_session_state(
        self,
        client: httpx.AsyncClient,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
    ) -> None:
        user_id = concepts["Limits"].user_id
        result = await session_manager.start(user_id, StartSessionRequest(session_type="teach"))
        with pytest.raises(ConflictError):
            await session_manager.handle(
                user_id, result.session_id, ClientMessage(type="request_hint")
            )
        stranger = await provision(client, auth("stranger"))
        with pytest.raises(NotFoundError):
            await session_manager.handle(
                stranger, result.session_id, ClientMessage(type="end_session")
            )
        with pytest.raises(NotFoundError):
            await session_manager.resume(stranger, result.session_id)

    async def test_busy_session_is_rejected(
        self, app: FastAPI, session_manager: SessionManager, concepts: dict[str, Concept]
    ) -> None:
        user_id = concepts["Limits"].user_id
        result = await session_manager.start(user_id, StartSessionRequest())
        await app.state.redis.set(f"lock:session:{result.session_id}", "other-worker")
        with pytest.raises(ConflictError, match="busy"):
            await ack(session_manager, user_id, result)


class TestRecovery:
    async def test_resumes_from_postgres_when_redis_state_is_lost(
        self, app: FastAPI, session_manager: SessionManager, concepts: dict[str, Concept]
    ) -> None:
        """AC-4.5."""
        user_id = concepts["Limits"].user_id
        result = await session_manager.start(user_id, StartSessionRequest(session_type="practice"))
        open_question = result.current_question
        await app.state.redis.delete(state_key(result.session_id))  # Redis lost the hot copy

        restarted = SessionManager(  # e.g. after an API restart
            settings=app.state.settings,
            sessionmaker=app.state.sessionmaker,
            llm=app.state.llm,
            recorder=app.state.ai_recorder,
            prompts=session_manager.prompts,
            redis=app.state.redis,
            vectors=app.state.vector_store,
        )
        resumed = await restarted.resume(user_id, result.session_id)
        assert resumed.awaiting == "answer" and resumed.current_question == open_question
        assert types(resumed) == ["question"]

        after = await answer(restarted, user_id, resumed, correct_answer(open_question))
        assert event(after, "evaluation")["is_correct"]
        assert (await state_of(app, result.session_id))["turn"] == 2  # Redis repopulated

    async def test_stale_redis_copy_is_ignored(
        self, app: FastAPI, session_manager: SessionManager, concepts: dict[str, Concept]
    ) -> None:
        user_id = concepts["Limits"].user_id
        sm = session_manager
        start = await sm.start(user_id, StartSessionRequest(session_type="practice"))
        stale = await app.state.redis.get(state_key(start.session_id))
        moved_on = await answer(sm, user_id, start, correct_answer(start.current_question))
        await app.state.redis.set(state_key(start.session_id), stale)  # older turn put back
        resumed = await sm.resume(user_id, start.session_id)
        assert resumed.interaction_count == moved_on.interaction_count
        assert resumed.current_question == moved_on.current_question

    async def test_resume_shows_pending_explanation_and_summary(
        self, session_manager: SessionManager, concepts: dict[str, Concept]
    ) -> None:
        user_id = concepts["Limits"].user_id
        start = await session_manager.start(user_id, StartSessionRequest(session_type="teach"))
        resumed = await session_manager.resume(user_id, start.session_id)
        assert types(resumed) == ["explanation_start", "explanation_chunk", "explanation_end"]
        ended = await session_manager.end(user_id, start.session_id)
        assert event(ended, "session_ended")["summary"]["end_reason"] == "student_ended"
        assert types(await session_manager.resume(user_id, start.session_id)) == ["session_ended"]

    async def test_transient_ai_failure_keeps_state_for_retry(
        self,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        user_id = concepts["Limits"].user_id
        start = await session_manager.start(user_id, StartSessionRequest(session_type="teach"))
        fake_llm.fail_kinds = {"question": LLMServerError("blip")}
        failed = await ack(session_manager, user_id, start)
        assert types(failed) == ["error"] and event(failed, "error")["code"] == "LLM_UNAVAILABLE"
        assert (
            failed.awaiting == "acknowledgement"
            and failed.interaction_count == start.interaction_count
        )
        assert await db.scalar(select(func.count()).select_from(QuestionAttempt)) == 0

        fake_llm.fail_kinds = {}
        retried = await ack(session_manager, user_id, start)
        assert retried.awaiting == "answer"

    async def test_permanent_ai_failure_ends_gracefully(
        self,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        user_id = concepts["Limits"].user_id
        start = await session_manager.start(user_id, StartSessionRequest(session_type="teach"))
        fake_llm.fail_kinds = {"question": LLMResponseError("bad request")}
        ended = await ack(session_manager, user_id, start)
        assert types(ended)[0] == "error" and types(ended)[-1] == "session_ended"
        assert ended.status == "completed"
        assert event(ended, "session_ended")["summary"]["end_reason"] == "ai_unavailable"
        row = await db.scalar(select(LearningSession).where(LearningSession.id == start.session_id))
        assert row is not None and row.status == "completed"

    async def test_session_token_budget(
        self,
        app: FastAPI,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
        db: AsyncSession,
    ) -> None:
        user_id = concepts["Limits"].user_id
        start = await session_manager.start(user_id, StartSessionRequest(session_type="teach"))
        trace = await db.scalar(select(AITrace).where(AITrace.session_id == start.session_id))
        assert trace is not None
        trace.total_tokens = app.state.settings.ai_session_token_budget
        await db.commit()
        ended = await ack(session_manager, user_id, start)
        assert (
            types(ended)[0] == "error" and event(ended, "error")["code"] == "SESSION_TOKEN_BUDGET"
        )
        assert ended.status == "completed"
        assert event(ended, "session_ended")["summary"]["end_reason"] == "token_budget"


class TestContentAndReuse:
    async def test_tutor_is_grounded_in_uploaded_material(
        self,
        client: httpx.AsyncClient,
        session_manager: SessionManager,
        uploader: Any,
        processor: Any,
        fake_queue: Any,
        db: AsyncSession,
    ) -> None:
        from app.db.models import AIInteraction
        from tests.fakes import concept_text
        from tests.integration.conftest import drain

        await uploader.upload(
            H,
            concept_text(
                "Photosynthesis", "Plants turn light into chemical energy", filler=8
            ).encode(),
        )
        assert await drain(processor, fake_queue) == ["ready"]
        user_id = await provision(client, H)
        result = await session_manager.start(user_id, StartSessionRequest(session_type="teach"))
        assert result.objective["target_concepts"][0]["name"] == "Photosynthesis"
        logged = await db.scalar(
            select(SessionEvent.payload).where(SessionEvent.event_type == "explanation_given")
        )
        assert logged is not None and logged["materials"] >= 1
        explanation = await db.scalar(
            select(AIInteraction).where(AIInteraction.purpose == "explanation_intro")
        )
        assert explanation is not None
        prompt = json.dumps((explanation.metadata_ or {})["request"])
        assert (
            "---MATERIAL---" in prompt and "Photosynthesis is studied by working through" in prompt
        )
        assert (explanation.metadata_ or {})["prompt_name"] == "tutor/explain_concept"
        begin_trace = await db.scalar(
            select(AITrace.id).where(
                AITrace.session_id == result.session_id, AITrace.operation == "session_begin"
            )
        )
        assert explanation.trace_id == begin_trace

    async def test_stored_questions_are_reused_across_sessions(
        self,
        session_manager: SessionManager,
        concepts: dict[str, Concept],
        fake_llm: FakeLLMProvider,
        db: AsyncSession,
    ) -> None:
        user_id = concepts["Limits"].user_id
        req = StartSessionRequest(session_type="practice", concept_ids=[concepts["Vectors"].id])
        first = await session_manager.start(user_id, req)
        await session_manager.end(user_id, first.session_id)
        generated = fake_llm.calls.count("question")
        second = await session_manager.start(user_id, req)
        assert fake_llm.calls.count("question") == generated  # served from stored questions
        assert second.current_question is not None
        reused = await db.scalar(
            select(SessionEvent.payload).where(
                SessionEvent.session_id == second.session_id,
                SessionEvent.event_type == "question_asked",
            )
        )
        assert reused is not None and reused["reused"] is True
