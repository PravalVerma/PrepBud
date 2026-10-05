"""Session decision rules (LEARNING_ENGINE §7, AI_SYSTEM_DESIGN §6.3) and the message protocol."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import LearningEngineSettings
from app.core.exceptions import ConflictError
from app.services.learning_engine.orchestrator import (
    SessionEngine,
    decide_next,
    elapsed_minutes,
    explain_kind_after_failure,
    initial_action,
)
from app.services.learning_engine.session_manager import public_question, validate_message
from app.services.learning_engine.state import (
    NODE_STATES,
    STATUS_TRANSITIONS,
    VALID_TRANSITIONS,
    ClientMessage,
    SessionState,
    StartSessionRequest,
    new_state,
)

CFG = LearningEngineSettings()
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
A, B, C = "a" * 8, "b" * 8, "c" * 8


def state(**overrides: Any) -> SessionState:
    s = new_state(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session_type="mixed",
        learning_goal_id=None,
        time_budget_minutes=30,
        max_interactions=20,
        max_concepts=5,
        requested_concepts=[],
        started_at=NOW,
    )
    s.update(
        status="active",
        target_concepts=[A, B, C],
        concepts_remaining=[B, C],
        current_concept_id=A,
        mastery_snapshot={A: 0.5, B: 0.1, C: 0.3},
        concept_info={A: {"difficulty": 0.5}, B: {"difficulty": 0.8}, C: {"difficulty": 0.2}},
    )
    s.update(overrides)  # type: ignore[typeddict-item]
    return s


def attempt(
    concept: str = A, correct: bool = True, score: float | None = None, t: int | None = None
) -> dict[str, Any]:
    return {
        "concept_id": concept,
        "is_correct": correct,
        "score": score if score is not None else float(correct),
        "time_taken": t,
    }


class TestDecideNext:
    def test_after_explanation_practice(self) -> None:
        d = decide_next(state(current_action="explain"), CFG, NOW)
        assert (d.action, d.reason) == ("practice", "check_understanding")

    def test_success_keeps_practising(self) -> None:
        s = state(
            current_action="practice",
            attempts=[attempt()],
            mastery_snapshot={A: 0.65, B: 0.1, C: 0.3},
        )
        assert decide_next(s, CFG, NOW).action == "practice"

    def test_review_mode_keeps_reviewing(self) -> None:
        s = state(
            current_action="review", attempts=[attempt()], mastery_snapshot={A: 0.65, B: 0, C: 0}
        )
        assert decide_next(s, CFG, NOW).action == "review"

    def test_bad_score_triggers_re_explanation(self) -> None:
        s = state(
            current_action="practice",
            attempts=[attempt(correct=False, score=0.1)],
            explanations={A: 1},
        )
        d = decide_next(s, CFG, NOW)
        assert (d.action, d.explain_kind) == ("explain", "retry")

    def test_low_mastery_after_wrong_answer_re_explains(self) -> None:
        wrong = attempt(correct=False, score=0.5)
        s = state(
            current_action="practice", attempts=[wrong], mastery_snapshot={A: 0.35, B: 0, C: 0}
        )
        assert decide_next(s, CFG, NOW).action == "explain"

    def test_correct_answer_keeps_practising_even_at_low_mastery(self) -> None:
        """Regression (found with a real model): no lecture after every right answer."""
        s = state(
            current_action="practice", attempts=[attempt()], mastery_snapshot={A: 0.05, B: 0, C: 0}
        )
        assert decide_next(s, CFG, NOW).action == "practice"

    def test_mastered_concept_moves_on(self) -> None:
        s = state(
            current_action="practice",
            attempts=[attempt()],
            mastery_snapshot={A: 0.85, B: 0.1, C: 0.3},
        )
        d = decide_next(s, CFG, NOW)
        assert (d.action, d.switch_to, d.reason) == ("switch", B, "concept_mastered")

    def test_attempt_limit_moves_on(self) -> None:
        s = state(
            current_action="practice",
            attempts=[attempt(score=0.5)],
            concept_attempts={A: CFG.max_attempts_per_concept},
        )
        assert decide_next(s, CFG, NOW).reason == "attempt_limit"

    def test_last_concept_done_wraps(self) -> None:
        s = state(
            current_action="practice",
            attempts=[attempt()],
            concepts_remaining=[],
            mastery_snapshot={A: 0.9, B: 0.1, C: 0.3},
        )
        d = decide_next(s, CFG, NOW)
        assert (d.action, d.end_reason) == ("wrap", "concepts_complete")

    def test_all_targets_mastered_wraps(self) -> None:
        s = state(mastery_snapshot={A: 0.9, B: 0.85, C: 0.81})
        assert decide_next(s, CFG, NOW).end_reason == "all_mastered"

    def test_interaction_limit(self) -> None:
        assert decide_next(state(interaction_count=20), CFG, NOW).end_reason == "interaction_limit"

    def test_time_budget(self) -> None:
        later = NOW + timedelta(minutes=31)
        assert decide_next(state(), CFG, later).end_reason == "time_budget"
        assert elapsed_minutes(state(), later) == pytest.approx(31)

    def test_frustration_guard_switches_to_easier_concept(self) -> None:
        """AC-4.8: five consecutive failures activate the guard."""
        fails = [attempt(correct=False, score=0.0) for _ in range(5)]
        s = state(current_action="practice", attempts=fails, consecutive_failures=5)
        d = decide_next(s, CFG, NOW)
        assert d.frustration and d.reason == "frustration"
        assert (d.action, d.switch_to) == ("switch", C)  # C is the easiest remaining
        assert d.message and "tricky" in d.message

    def test_frustration_guard_on_last_concept_explains_with_example(self) -> None:
        fails = [attempt(correct=False, score=0.0) for _ in range(5)]
        s = state(
            current_action="practice", attempts=fails, consecutive_failures=5, concepts_remaining=[]
        )
        d = decide_next(s, CFG, NOW)
        assert (d.action, d.explain_kind) == ("explain", "worked_example")

    def test_persistent_frustration_ends_session(self) -> None:
        fails = [attempt(correct=False, score=0.0) for _ in range(5)]
        s = state(
            current_action="practice",
            attempts=fails,
            consecutive_failures=5,
            frustration_activations=1,
        )
        d = decide_next(s, CFG, NOW)
        assert (d.action, d.end_reason) == ("wrap", "frustration")
        assert d.message and "break" in d.message

    def test_four_failures_do_not_trigger(self) -> None:
        fails = [attempt(correct=False, score=0.0) for _ in range(4)]
        s = state(current_action="practice", attempts=fails, consecutive_failures=4)
        assert not decide_next(s, CFG, NOW).frustration

    def test_default_falls_back_to_initial_action(self) -> None:
        assert decide_next(state(current_action=None), CFG, NOW).action == "practice"
        s = state(current_action=None, mastery_snapshot={A: 0.1, B: 0, C: 0})
        assert decide_next(s, CFG, NOW).action == "explain"


class TestInitialAction:
    @pytest.mark.parametrize(
        ("session_type", "mastery", "review", "expected"),
        [
            ("teach", 0.5, False, "explain"),
            ("teach", 0.9, False, "practice"),
            ("practice", 0.0, False, "practice"),
            ("review", 0.0, False, "review"),
            ("mixed", 0.1, False, "explain"),
            ("mixed", 0.5, False, "practice"),
            ("mixed", 0.5, True, "review"),
            ("practice", 0.5, True, "review"),
        ],
    )
    def test_by_session_type(
        self, session_type: str, mastery: float, review: bool, expected: str
    ) -> None:
        s = state(
            session_type=session_type,
            mastery_snapshot={A: mastery},
            review_concepts=[A] if review else [],
        )
        assert initial_action(s, A, CFG) == expected

    def test_explanation_escalation(self) -> None:
        assert explain_kind_after_failure(state(explanations={}), A) == "intro"
        assert explain_kind_after_failure(state(explanations={A: 1}), A) == "retry"
        assert explain_kind_after_failure(state(explanations={A: 2}), A) == "worked_example"
        high = state(explanations={A: 1}, mastery_snapshot={A: 0.65})
        assert explain_kind_after_failure(high, A) == "socratic"


class TestRouting:
    @pytest.mark.parametrize(
        ("kind", "node"),
        [
            ("end_session", "wrap"),
            ("student_question", "respond"),
            ("request_hint", "respond"),
            ("student_acknowledge", "acknowledge"),
            ("student_response", "evaluate"),
        ],
    )
    def test_entry(self, kind: str, node: str) -> None:
        assert SessionEngine.route_entry(state(pending_input={"type": kind})) == node

    def test_new_session_starts_at_init(self) -> None:
        assert SessionEngine.route_entry(state(target_concepts=[])) == "init"
        planned = state(status="initialising", plan_only=True, pending_input={"type": "begin"})
        assert SessionEngine.route_entry(planned) == "begin"
        assert SessionEngine.route_after_plan(planned) == "__end__"

    def test_unknown_input_ends(self) -> None:
        from langgraph.graph import END

        assert SessionEngine.route_entry(state(pending_input=None)) == END

    def test_route_action(self) -> None:
        assert SessionEngine.route_action(state(next_action="review")) == "review"
        assert SessionEngine.route_action(state(next_action="bogus")) == "wrap"
        assert SessionEngine.route_action(state(next_action=None)) == "wrap"


class TestProtocol:
    def test_state_machine_tables_are_consistent(self) -> None:
        assert set(NODE_STATES.values()) == set(VALID_TRANSITIONS)
        assert VALID_TRANSITIONS["SCHEDULE_REVIEW"] == frozenset()
        assert "completed" in STATUS_TRANSITIONS["active"] and not STATUS_TRANSITIONS["completed"]

    @pytest.mark.parametrize(
        "raw",
        [
            {
                "type": "student_response",
                "payload": {"content": "2x", "question_id": "q", "time_taken_seconds": 12},
            },
            {"type": "student_question", "payload": {"content": "Why?"}},
            {"type": "student_acknowledge", "payload": {"understood": False}},
            {"type": "request_hint", "payload": {}},
            {"type": "end_session"},
        ],
    )
    def test_valid_messages(self, raw: dict[str, Any]) -> None:
        ClientMessage.model_validate(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            {"type": "shout"},
            {"type": "student_response", "payload": {"content": "", "question_id": "q"}},
            {"type": "student_response", "payload": {"content": "x"}},
            {
                "type": "student_response",
                "payload": {"content": "x", "question_id": "q", "time_taken_seconds": -1},
            },
            {
                "type": "student_response",
                "payload": {"content": "x", "question_id": "q", "time_taken_seconds": True},
            },
            {"type": "student_response", "payload": {"content": "x" * 4001, "question_id": "q"}},
            {"type": "student_question", "payload": {"content": "bad\x00"}},
            {"type": "student_acknowledge", "payload": {"understood": "yes"}},
            {"type": "end_session", "extra": 1},
        ],
    )
    def test_invalid_messages(self, raw: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ClientMessage.model_validate(raw)

    def test_messages_must_fit_what_the_session_awaits(self) -> None:
        question = {
            "id": "q1",
            "concept_id": A,
            "type": "mcq",
            "difficulty": 0.4,
            "content": "?",
            "options": [],
            "hints": ["h"],
            "hints_given": 0,
            "mode": "practice",
        }
        answering = state(awaiting="answer", current_question=question)
        validate_message(
            answering,
            ClientMessage(type="student_response", payload={"content": "x", "question_id": "q1"}),
        )
        validate_message(answering, ClientMessage(type="request_hint"))
        validate_message(
            answering, ClientMessage(type="student_question", payload={"content": "?"})
        )
        with pytest.raises(ConflictError, match="no longer open"):
            validate_message(
                answering,
                ClientMessage(
                    type="student_response", payload={"content": "x", "question_id": "old"}
                ),
            )
        with pytest.raises(ConflictError):
            validate_message(
                answering, ClientMessage(type="student_acknowledge", payload={"understood": True})
            )
        acking = state(awaiting="acknowledgement")
        validate_message(
            acking, ClientMessage(type="student_acknowledge", payload={"understood": True})
        )
        with pytest.raises(ConflictError):
            validate_message(acking, ClientMessage(type="request_hint"))
        with pytest.raises(ConflictError):
            validate_message(
                state(awaiting="none"),
                ClientMessage(type="student_question", payload={"content": "?"}),
            )
        validate_message(state(awaiting="none"), ClientMessage(type="end_session"))
        public = public_question(question)
        assert public is not None and "hints" not in public and public["hints_available"] == 1
        assert public_question(None) is None

    def test_start_request_validation(self) -> None:
        assert StartSessionRequest().session_type == "mixed"
        for bad in (
            {"session_type": "nap"},
            {"time_budget_minutes": 2},
            {"preferences": {"max_concepts": 0}},
            {"preferences": {"max_concepts": True}},
            {"x": 1},
        ):
            with pytest.raises(ValidationError):
                StartSessionRequest.model_validate(bad)


def test_frustration_needs_fresh_failures_after_an_activation() -> None:
    """Regression: the guard must not re-fire on old evidence right after activating."""
    fails = [attempt(correct=False, score=0.0) for _ in range(6)]
    s = state(
        current_action="practice",
        attempts=fails,
        consecutive_failures=1,
        frustration_activations=1,
        frustration_reset_index=5,
    )
    assert not decide_next(s, CFG, NOW).frustration
    s["consecutive_failures"] = 5
    assert decide_next(s, CFG, NOW).frustration


def test_misconception_message_is_non_judgemental() -> None:
    from app.services.learning_engine.orchestrator import misconception_message

    text = misconception_message("Sign error", "Flips the sign when differentiating.")
    assert text.startswith(
        "I notice a common mix-up here: Sign error (Flips the sign when differentiating)"
    )
    assert "normal step" in text and "wrong" not in text.lower()
    assert misconception_message("Confuses terms", "Confuses terms") == misconception_message(
        "Confuses terms", ""
    )
