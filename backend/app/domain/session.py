"""Learning session schemas (API_CONTRACT §3.8–3.9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.learning_engine.state import ClientMessage


class SessionCreated(BaseModel):
    session_id: uuid.UUID
    status: str
    websocket_url: str
    objective: dict[str, Any]


class SessionView(BaseModel):
    """Where a session stands: what the client should show and what it may send next."""

    session_id: uuid.UUID
    status: str
    awaiting: str
    objective: dict[str, Any]
    current_question: dict[str, Any] | None = None
    current_concept_id: str | None = None
    interaction_count: int = 0
    summary: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class SessionListItem(BaseModel):
    id: uuid.UUID
    session_type: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    interaction_count: int
    concepts: list[str]
    summary: dict[str, Any] | None


class SessionEventRead(BaseModel):
    index: int
    type: str
    concept_id: uuid.UUID | None
    payload: dict[str, Any]
    created_at: datetime | None


class SessionDetail(SessionListItem):
    learning_goal_id: uuid.UUID | None
    objective: dict[str, Any]
    awaiting: str | None
    current_question: dict[str, Any] | None
    events: list[SessionEventRead]


class WsTicket(BaseModel):
    ticket: str
    expires_in_seconds: int
    websocket_path: str


class TurnRequest(BaseModel):
    """Body of ``POST /sessions/{id}/messages``: a client message, or ``begin``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "begin",
        "student_response",
        "student_question",
        "student_acknowledge",
        "request_hint",
        "end_session",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)

    def client_message(self) -> ClientMessage | None:
        return None if self.type == "begin" else ClientMessage.model_validate(self.model_dump())
