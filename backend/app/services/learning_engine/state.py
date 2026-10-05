"""Learning session state (AI_SYSTEM_DESIGN §6.2) and the message protocol it reacts to.

The whole state is JSON-serialisable: it is checkpointed to Redis
(``session:{session_id}:state``) and PostgreSQL after every turn, so an interrupted
session resumes exactly where it stopped (AC-4.5).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.learning_engine.concept_selector import SESSION_TYPES

Awaiting = Literal["none", "acknowledgement", "answer", "ended"]
Action = Literal["explain", "practice", "review", "wrap"]
SessionStatus = Literal["initialising", "active", "paused", "completed", "abandoned"]

#: DOMAIN_MODEL §3.2 lifecycle.
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "initialising": frozenset({"active", "abandoned"}),
    "active": frozenset({"paused", "completed", "abandoned"}),
    "paused": frozenset({"active", "abandoned"}),
    "completed": frozenset(),
    "abandoned": frozenset(),
}

#: PRODUCT_REQUIREMENTS §4.3 — graph node names map onto these states.
NODE_STATES = {
    "init": "INITIALISE",
    "plan": "PLAN_SESSION",
    "explain": "EXPLAIN",
    "practice": "PRACTICE",
    "review": "REVIEW",
    "evaluate": "EVALUATE_RESPONSE",
    "update": "UPDATE_MASTERY",
    "decide": "DECIDE_NEXT",
    "wrap": "WRAP_UP",
    "schedule": "SCHEDULE_REVIEW",
}
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "INITIALISE": frozenset({"PLAN_SESSION"}),
    "PLAN_SESSION": frozenset({"EXPLAIN", "PRACTICE", "REVIEW"}),
    "EXPLAIN": frozenset({"EVALUATE_RESPONSE", "PRACTICE", "EXPLAIN"}),
    "PRACTICE": frozenset({"EVALUATE_RESPONSE"}),
    "REVIEW": frozenset({"EVALUATE_RESPONSE"}),
    "EVALUATE_RESPONSE": frozenset({"UPDATE_MASTERY"}),
    "UPDATE_MASTERY": frozenset({"DECIDE_NEXT"}),
    "DECIDE_NEXT": frozenset({"EXPLAIN", "PRACTICE", "REVIEW", "WRAP_UP"}),
    "WRAP_UP": frozenset({"SCHEDULE_REVIEW"}),
    "SCHEDULE_REVIEW": frozenset(),
}


class SessionState(TypedDict, total=False):
    # identity
    session_id: str
    user_id: str
    learning_goal_id: str | None
    session_type: str
    status: str
    awaiting: str
    turn: int
    # objective
    requested_concepts: list[str]
    target_concepts: list[str]
    review_concepts: list[str]
    concepts_remaining: list[str]
    concepts_covered: list[str]
    current_concept_id: str | None
    concept_info: dict[str, dict[str, Any]]
    objective: dict[str, Any]
    # control
    current_action: str | None
    next_action: str | None
    explain_kind: str | None
    action_history: list[str]
    state_trail: list[str]
    interaction_count: int
    max_interactions: int
    time_budget_minutes: int
    max_concepts: int
    started_at: str
    # student model
    student_profile: dict[str, Any]
    mastery_snapshot: dict[str, float]
    initial_mastery: dict[str, float]
    # assessment
    current_question: dict[str, Any] | None
    asked_question_ids: list[str]
    recent_question_texts: list[str]
    question_types: dict[str, list[str]]
    attempts: list[dict[str, Any]]
    concept_attempts: dict[str, int]
    consecutive_failures: int
    difficulty_offset: float
    frustration_activations: int
    frustration_reset_index: int  # attempts before this index don't count again
    last_evaluation: dict[str, Any] | None
    mastery_updates: list[dict[str, Any]]
    misconceptions_found: list[dict[str, Any]]
    # tutor
    explanations: dict[str, int]
    last_explanation: str
    history: list[str]
    # turn I/O
    pending_input: dict[str, Any] | None
    outbox: list[dict[str, Any]]
    log: list[dict[str, Any]]
    event_index: int
    # ending
    end_reason: str | None
    summary: dict[str, Any] | None
    no_llm: bool


def new_state(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    session_type: str,
    learning_goal_id: uuid.UUID | None,
    time_budget_minutes: int,
    max_interactions: int,
    max_concepts: int,
    requested_concepts: list[uuid.UUID],
    started_at: datetime,
) -> SessionState:
    return SessionState(
        session_id=str(session_id),
        user_id=str(user_id),
        learning_goal_id=str(learning_goal_id) if learning_goal_id else None,
        session_type=session_type,
        status="initialising",
        awaiting="none",
        turn=0,
        requested_concepts=[str(c) for c in requested_concepts],
        target_concepts=[],
        review_concepts=[],
        concepts_remaining=[],
        concepts_covered=[],
        current_concept_id=None,
        concept_info={},
        objective={},
        current_action=None,
        next_action=None,
        explain_kind=None,
        action_history=[],
        state_trail=[],
        interaction_count=0,
        max_interactions=max_interactions,
        time_budget_minutes=time_budget_minutes,
        max_concepts=max_concepts,
        started_at=started_at.isoformat(),
        student_profile={},
        mastery_snapshot={},
        initial_mastery={},
        current_question=None,
        asked_question_ids=[],
        recent_question_texts=[],
        question_types={},
        attempts=[],
        concept_attempts={},
        consecutive_failures=0,
        difficulty_offset=0.0,
        frustration_activations=0,
        frustration_reset_index=0,
        last_evaluation=None,
        mastery_updates=[],
        misconceptions_found=[],
        explanations={},
        last_explanation="",
        history=[],
        pending_input=None,
        outbox=[],
        log=[],
        event_index=0,
        end_reason=None,
        summary=None,
        no_llm=False,
    )


# --- Client → server messages (API_CONTRACT §3.9, plus request_hint) ---------------------------

ClientMessageType = Literal[
    "student_response", "student_question", "student_acknowledge", "request_hint", "end_session"
]


class ClientMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ClientMessageType
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _payload(self) -> ClientMessage:
        p = self.payload
        if self.type in ("student_response", "student_question"):
            content = p.get("content")
            limit = 4000 if self.type == "student_response" else 2000
            if not isinstance(content, str) or not content.strip() or len(content) > limit:
                raise ValueError(f"payload.content must be 1–{limit} characters")
            if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in content):
                raise ValueError("payload.content must not contain control characters")
        if self.type == "student_response":
            if not isinstance(p.get("question_id"), str):
                raise ValueError("payload.question_id is required")
            taken = p.get("time_taken_seconds")
            if taken is not None and (
                not isinstance(taken, int) or isinstance(taken, bool) or taken < 0
            ):
                raise ValueError("payload.time_taken_seconds must be a non-negative integer")
        if self.type == "student_acknowledge" and not isinstance(p.get("understood"), bool):
            raise ValueError("payload.understood must be true or false")
        return self


class StartSessionRequest(BaseModel):
    """Body of ``POST /sessions`` (API_CONTRACT §3.8); ``concept_ids`` = student's choice."""

    model_config = ConfigDict(extra="forbid")

    session_type: str = "mixed"
    learning_goal_id: uuid.UUID | None = None
    time_budget_minutes: int | None = Field(default=None, ge=5, le=180)
    concept_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    preferences: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in SESSION_TYPES:
            raise ValueError(f"session_type must be one of {', '.join(SESSION_TYPES)}")
        return v

    @field_validator("preferences")
    @classmethod
    def _preferences(cls, v: dict[str, Any]) -> dict[str, Any]:
        mc = v.get("max_concepts")
        if mc is not None and (
            not isinstance(mc, int) or isinstance(mc, bool) or not 1 <= mc <= 10
        ):
            raise ValueError("preferences.max_concepts must be an integer between 1 and 10")
        return v
