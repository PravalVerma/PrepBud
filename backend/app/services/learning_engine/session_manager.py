"""Learning session lifecycle: start, turns, end, resume (AC-4.4, AC-4.5).

Each student message is one *turn*: load state → validate the message against what the
session is waiting for → run the orchestrator graph → persist. Persistence per turn:

* PostgreSQL — the ``learning_sessions`` row (status, objective, counters, summary), the
  ordered ``session_events`` log, and a durable checkpoint of the full state in
  ``learning_sessions.metadata.checkpoint``;
* Redis — the hot copy at ``session:{session_id}:state`` (TTL 2 h, DATA_MODEL §5).

On load the newer of the two copies wins (by turn number), so a lost or stale Redis entry
never loses progress. Turns on one session are serialised with a Redis lock.

AI failures: a transient error leaves the session exactly as it was (the client can
retry the same message); a permanent one (or an exhausted budget) ends the session
gracefully with a summary (product assumption #5).
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.cost_tracker import AICallContext, AIUsageRecorder
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import PromptManager
from app.ai.providers.base import AIBudgetExceededError, LLMError
from app.config import Settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from app.core.logging import get_logger
from app.db.models import AITrace, Concept, LearningGoal, LearningSession, SessionEvent
from app.integrations.qdrant import SectionVectorStore
from app.services.content.retriever import HybridRetriever
from app.services.learning_engine.orchestrator import (
    Emitter,
    EngineDeps,
    SessionEngine,
    SessionPlanningError,
)
from app.services.learning_engine.state import (
    ClientMessage,
    SessionState,
    StartSessionRequest,
    new_state,
)

logger = get_logger(__name__)

LOCK_TTL_SECONDS = 300
ERROR_MESSAGES = {
    "LLM_TIMEOUT": "The AI is taking longer than expected. Please try again.",
    "LLM_UNAVAILABLE": "The AI service is unavailable right now. Please try again in a moment.",
    "LLM_RATE_LIMITED": "The AI service is busy. Please try again in a moment.",
}


def state_key(session_id: uuid.UUID | str) -> str:
    return f"session:{session_id}:state"


@dataclass(slots=True)
class TurnResult:
    session_id: uuid.UUID
    status: str
    awaiting: str
    events: list[dict[str, Any]] = field(default_factory=list)
    objective: dict[str, Any] = field(default_factory=dict)
    current_question: dict[str, Any] | None = None
    current_concept_id: str | None = None
    interaction_count: int = 0
    summary: dict[str, Any] | None = None


def public_question(question: dict[str, Any] | None) -> dict[str, Any] | None:
    if not question:
        return None
    return {
        "question_id": question["id"],
        "concept_id": question["concept_id"],
        "type": question["type"],
        "difficulty": round(question["difficulty"], 3),
        "content": question["content"],
        "options": question["options"],
        "hints_available": len(question["hints"]) - question["hints_given"],
        "mode": question["mode"],
    }


def validate_message(state: SessionState, msg: ClientMessage) -> None:
    awaiting = state["awaiting"]
    if msg.type == "end_session":
        return
    if msg.type in ("student_response", "request_hint") and awaiting != "answer":
        raise ConflictError("No question is waiting for an answer", details={"awaiting": awaiting})
    if msg.type == "student_response":
        current = state.get("current_question") or {}
        if msg.payload["question_id"] != current.get("id"):
            raise ConflictError("That question is no longer open", details={"awaiting": awaiting})
    if msg.type == "student_acknowledge" and awaiting != "acknowledgement":
        raise ConflictError(
            "Nothing is waiting for an acknowledgement", details={"awaiting": awaiting}
        )
    if msg.type == "student_question" and awaiting not in ("acknowledgement", "answer"):
        raise ConflictError(
            "The session is not ready for questions", details={"awaiting": awaiting}
        )


class SessionManager:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        recorder: AIUsageRecorder,
        prompts: PromptManager,
        redis: Redis | None = None,
        vectors: SectionVectorStore | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.cfg = settings.learning_engine
        self.sessionmaker = sessionmaker
        self.llm = llm
        self.recorder = recorder
        self.prompts = prompts
        self.redis = redis
        self.vectors = vectors
        self.now = now

    # ---- engine plumbing ----------------------------------------------------------------------

    def _engine(self, db: AsyncSession, ai: AICallContext, emit: Emitter | None) -> SessionEngine:
        retriever = HybridRetriever(db, vectors=self.vectors, llm=self.llm, recorder=self.recorder)
        return SessionEngine(
            EngineDeps(
                session=db,
                settings=self.settings,
                llm=self.llm,
                prompts=self.prompts,
                retriever=retriever,
                ai=ai,
                emit=emit,
                redis=self.redis,
                now=self.now,
            )
        )

    async def _context(
        self, user_id: uuid.UUID, session_id: uuid.UUID, operation: str
    ) -> AICallContext:
        trace_id = await self.recorder.start_trace(
            user_id, operation, session_id=session_id, metadata={"session_id": str(session_id)}
        )
        return AICallContext(
            user_id=user_id,
            trace_id=trace_id,
            purpose="learning_session",
            metadata={"session_id": str(session_id)},
        )

    async def _lock(self, session_id: uuid.UUID) -> str | None:
        token = uuid.uuid4().hex
        if self.redis is None:
            return token
        try:
            ok = await self.redis.set(
                f"lock:session:{session_id}", token, nx=True, ex=LOCK_TTL_SECONDS
            )
        except Exception as exc:
            logger.warning("session lock unavailable", extra={"error": type(exc).__name__})
            return token
        return token if ok else None

    async def _unlock(self, session_id: uuid.UUID, token: str) -> None:
        if self.redis is None:
            return
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) end return 0"
        )
        try:
            await self.redis.eval(script, 1, f"lock:session:{session_id}", token)  # type: ignore[misc]
        except Exception as exc:
            logger.warning("session unlock failed", extra={"error": type(exc).__name__})

    # ---- state persistence ---------------------------------------------------------------------

    async def _row(
        self, db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> LearningSession:
        row = await db.scalar(
            select(LearningSession)
            .where(LearningSession.id == session_id, LearningSession.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("Session not found")
        return row

    async def load_state(self, row: LearningSession) -> SessionState | None:
        """Newest checkpoint from Redis or PostgreSQL."""
        durable = (row.metadata_ or {}).get("checkpoint")
        hot: dict[str, Any] | None = None
        if self.redis is not None:
            try:
                raw = await self.redis.get(state_key(row.id))
                hot = json.loads(raw) if raw else None
            except Exception as exc:
                logger.warning(
                    "session state cache unavailable", extra={"error": type(exc).__name__}
                )
        candidates = [c for c in (hot, durable) if isinstance(c, dict)]
        if not candidates:
            return None
        best = max(candidates, key=lambda c: int(c.get("turn", 0)))
        state = cast(SessionState, dict(best))
        state["outbox"], state["log"], state["pending_input"] = (
            [],
            list(state.get("log") or []),
            None,
        )
        return state

    async def _persist(self, db: AsyncSession, row: LearningSession, state: SessionState) -> None:
        index = state["event_index"]
        for entry in state["log"]:
            concept = entry.get("concept_id")
            db.add(
                SessionEvent(
                    session_id=row.id,
                    event_index=index,
                    event_type=entry["event_type"],
                    concept_id=uuid.UUID(concept) if concept else None,
                    payload=entry.get("payload") or {},
                )
            )
            index += 1
        state["event_index"] = index
        state["log"] = []
        checkpoint = {k: v for k, v in state.items() if k not in ("outbox", "pending_input")}
        row.status = state["status"]
        row.objective = state["objective"]
        row.interaction_count = state["interaction_count"]
        row.concepts_covered = [uuid.UUID(c) for c in state["concepts_covered"]]
        row.metadata_ = {
            **(row.metadata_ or {}),
            "checkpoint": checkpoint,
            "checkpoint_turn": state["turn"],
            "end_reason": state.get("end_reason"),
        }
        if state["status"] == "completed" and row.ended_at is None:
            now = self.now()
            row.ended_at = now
            started = row.started_at or datetime.fromisoformat(state["started_at"])
            row.duration_seconds = max(0, int((now - started).total_seconds()))
            row.summary = state.get("summary")
        await db.commit()
        if self.redis is not None:
            try:
                await self.redis.set(
                    state_key(row.id), json.dumps(checkpoint), ex=self.cfg.session_state_ttl_seconds
                )
            except Exception as exc:  # PostgreSQL holds the durable copy
                logger.warning(
                    "session state cache write failed", extra={"error": type(exc).__name__}
                )

    def _result(
        self, row_id: uuid.UUID, state: SessionState, events: list[dict[str, Any]] | None = None
    ) -> TurnResult:
        return TurnResult(
            session_id=row_id,
            status=state["status"],
            awaiting=state["awaiting"],
            events=list(state["outbox"] if events is None else events),
            objective=state["objective"],
            current_question=public_question(state.get("current_question")),
            current_concept_id=state.get("current_concept_id"),
            interaction_count=state["interaction_count"],
            summary=state.get("summary"),
        )

    # ---- lifecycle ------------------------------------------------------------------------------

    async def _create_row(self, user_id: uuid.UUID, req: StartSessionRequest) -> uuid.UUID:
        async with self.sessionmaker() as db:
            if (
                req.learning_goal_id is not None
                and await db.scalar(
                    select(LearningGoal.id).where(
                        LearningGoal.id == req.learning_goal_id, LearningGoal.user_id == user_id
                    )
                )
                is None
            ):
                raise NotFoundError("Learning goal not found")
            if req.concept_ids:
                owned = set(
                    (
                        await db.scalars(
                            select(Concept.id).where(
                                Concept.user_id == user_id, Concept.id.in_(req.concept_ids)
                            )
                        )
                    ).all()
                )
                if owned != set(req.concept_ids):
                    raise NotFoundError("Concept not found")
            row = LearningSession(
                user_id=user_id,
                learning_goal_id=req.learning_goal_id,
                session_type=req.session_type,
                status="initialising",
                objective={},
                metadata_={},
            )
            db.add(row)
            await db.commit()
            return row.id

    async def create(
        self, user_id: uuid.UUID, req: StartSessionRequest, *, emit: Emitter | None = None
    ) -> TurnResult:
        """INIT + PLAN only (no AI calls): returns the objective, status ``initialising``.

        The first explanation or question is produced by `begin`, once the client listens.
        """
        session_id = await self._create_row(user_id, req)
        state = new_state(
            session_id=session_id,
            user_id=user_id,
            session_type=req.session_type,
            learning_goal_id=req.learning_goal_id,
            time_budget_minutes=req.time_budget_minutes or self.cfg.default_time_budget_minutes,
            max_interactions=self.cfg.default_max_interactions,
            max_concepts=req.preferences.get("max_concepts") or self.cfg.max_concepts_per_session,
            requested_concepts=list(req.concept_ids),
            started_at=self.now(),
        )
        state["plan_only"] = True
        ai = await self._context(user_id, session_id, "session_plan")
        status = "failed"
        try:
            async with self.sessionmaker() as db:
                state = await self._engine(db, ai, emit).run_turn(state)
                row = await self._row(db, user_id, session_id)
                await self._persist(db, row, state)
            status = "completed"
            return self._result(session_id, state)
        except SessionPlanningError as exc:
            await self._discard(user_id, session_id)
            raise UnprocessableEntityError(str(exc), details={"reason": "NO_CONCEPTS"}) from exc
        finally:
            await self.recorder.finish_trace(ai.trace_id, status)  # type: ignore[arg-type]

    async def begin(
        self, user_id: uuid.UUID, session_id: uuid.UUID, *, emit: Emitter | None = None
    ) -> TurnResult:
        """Run the first action of a planned session; for a running one, re-send the view."""
        token = await self._lock(session_id)
        if token is None:
            raise ConflictError("The session is busy with another message; try again")
        try:
            return await self._turn(user_id, session_id, {"type": "begin", "payload": {}}, emit)
        finally:
            await self._unlock(session_id, token)

    async def start(
        self, user_id: uuid.UUID, req: StartSessionRequest, *, emit: Emitter | None = None
    ) -> TurnResult:
        """`create` + `begin` in one call; an AI failure discards the new session (503)."""
        created = await self.create(user_id, req, emit=emit)
        result = await self.begin(user_id, created.session_id, emit=emit)
        error = next((e for e in result.events if e["type"] == "error"), None)
        if error is not None:
            await self._discard(user_id, created.session_id)
            code = error["payload"]["code"]
            raise ServiceUnavailableError(
                ERROR_MESSAGES.get(code, "The AI service could not start the session."),
                details={"reason": code},
            )
        return result

    async def _discard(self, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        async with self.sessionmaker() as db:
            row = await db.scalar(
                select(LearningSession).where(
                    LearningSession.id == session_id, LearningSession.user_id == user_id
                )
            )
            if row is not None:
                await db.delete(row)
                await db.commit()

    async def _tokens_used(self, db: AsyncSession, session_id: uuid.UUID) -> int:
        return int(
            await db.scalar(
                select(func.coalesce(func.sum(AITrace.total_tokens), 0)).where(
                    AITrace.session_id == session_id
                )
            )
            or 0
        )

    # ---- pause / resume -------------------------------------------------------------------

    def _apply_pause(self, state: SessionState) -> None:
        state["status"] = "paused"
        state["paused_at"] = self.now().isoformat()
        state["log"].append({"event_type": "session_paused", "concept_id": None, "payload": {}})

    def _apply_unpause(self, state: SessionState) -> None:
        paused_at = state.get("paused_at")
        if paused_at:
            gap = (self.now() - datetime.fromisoformat(str(paused_at))).total_seconds()
            total = float(state.get("paused_seconds") or 0) + max(0.0, gap)
            state["paused_seconds"] = round(total, 1)
        state["paused_at"] = None
        state["status"] = "active"
        state["log"].append({"event_type": "session_resumed", "concept_id": None, "payload": {}})

    async def _transition(
        self, user_id: uuid.UUID, session_id: uuid.UUID, change: Callable[[SessionState], bool]
    ) -> None:
        token = await self._lock(session_id)
        if token is None:
            raise ConflictError("The session is busy with another message; try again")
        try:
            async with self.sessionmaker() as db:
                row = await self._row(db, user_id, session_id)
                if row.status in ("completed", "abandoned"):
                    raise ConflictError("This session has ended", details={"status": row.status})
                state = await self.load_state(row)
                if state is None:
                    raise ConflictError("The session state could not be recovered")
                if change(state):
                    state["turn"] += 1
                    await self._persist(db, row, state)
        finally:
            await self._unlock(session_id, token)

    async def pause(self, user_id: uuid.UUID, session_id: uuid.UUID) -> TurnResult:
        """Stop the clock (the student left or the connection dropped). Idempotent."""

        def change(state: SessionState) -> bool:
            if state["status"] != "active":
                return False
            self._apply_pause(state)
            return True

        await self._transition(user_id, session_id, change)
        return await self.resume(user_id, session_id)

    async def unpause(self, user_id: uuid.UUID, session_id: uuid.UUID) -> TurnResult:
        """Restart the clock and return what the student should see. Idempotent."""

        def change(state: SessionState) -> bool:
            if state["status"] != "paused":
                return False
            self._apply_unpause(state)
            return True

        await self._transition(user_id, session_id, change)
        return await self.resume(user_id, session_id)

    # ---- turns ---------------------------------------------------------------------------

    async def handle(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        msg: ClientMessage,
        *,
        emit: Emitter | None = None,
    ) -> TurnResult:
        token = await self._lock(session_id)
        if token is None:
            raise ConflictError("The session is busy with another message; try again")
        try:
            return await self._turn(user_id, session_id, msg.model_dump(), emit, msg=msg)
        finally:
            await self._unlock(session_id, token)

    async def _turn(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        pending: dict[str, Any],
        emit: Emitter | None,
        *,
        msg: ClientMessage | None = None,
    ) -> TurnResult:
        kind = pending["type"]
        async with self.sessionmaker() as db:
            row = await self._row(db, user_id, session_id)
            if row.status in ("completed", "abandoned"):
                raise ConflictError("This session has ended", details={"status": row.status})
            state = await self.load_state(row)
            if state is None:
                raise ConflictError("The session state could not be recovered")
            if kind == "begin" and not state.get("plan_only"):
                return await self.resume(user_id, session_id)  # already begun
            if msg is not None:
                if state.get("plan_only") and msg.type != "end_session":
                    raise ConflictError(
                        "The session has not started yet", details={"awaiting": "begin"}
                    )
                validate_message(state, msg)
            if state["status"] == "paused":
                self._apply_unpause(state)  # any activity resumes a paused session
            pristine = copy.deepcopy(state)

            over_budget = (
                await self._tokens_used(db, session_id) >= self.settings.ai_session_token_budget
            )
            notice: dict[str, Any] | None = None
            if over_budget and kind != "end_session":
                state["end_reason"] = "token_budget"
                state["no_llm"] = True
                pending = {"type": "end_session", "payload": {}}
                notice = {
                    "type": "error",
                    "payload": {
                        "code": "SESSION_TOKEN_BUDGET",
                        "message": (
                            "This session has reached its AI usage limit, so we are wrapping up."
                        ),
                        "retryable": False,
                    },
                }
                if emit is not None:
                    await emit(notice)
            state["pending_input"] = pending
            state["outbox"] = []
            state["turn"] += 1
            if pending["type"] not in ("end_session", "begin"):
                state["interaction_count"] += 1

            ai = await self._context(user_id, session_id, f"session_{pending['type']}")
            try:
                state = await self._engine(db, ai, emit).run_turn(state)
            except LLMError as exc:
                await db.rollback()
                await self.recorder.finish_trace(ai.trace_id, "failed")
                return await self._on_ai_failure(user_id, session_id, pristine, exc, emit)
            await self._persist(db, row, state)
            await self.recorder.finish_trace(ai.trace_id, "completed")
            events = state["outbox"] if notice is None else [notice, *state["outbox"]]
            return self._result(session_id, state, events)

    async def _on_ai_failure(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        pristine: SessionState,
        exc: LLMError,
        emit: Emitter | None,
    ) -> TurnResult:
        transient = exc.retryable and not isinstance(exc, AIBudgetExceededError)
        if transient:
            message = ERROR_MESSAGES.get(exc.code, "Something went wrong with the AI service.")
        elif isinstance(exc, AIBudgetExceededError):
            message = "Your daily AI budget has been reached, so we are wrapping up for today."
        else:
            message = "The AI service is not responding properly, so we are wrapping up."
        error = {
            "type": "error",
            "payload": {"code": exc.code, "message": message, "retryable": transient},
        }
        if emit is not None:
            await emit(error)
        if transient:
            logger.warning(
                "session turn failed; state kept for retry",
                extra={"session_id": str(session_id), "error": exc.code},
            )
            return self._result(session_id, pristine, [error])
        # Permanent failure: end gracefully without further AI calls.
        logger.warning(
            "session ended after AI failure",
            extra={"session_id": str(session_id), "error": exc.code},
        )
        state = copy.deepcopy(pristine)
        state["pending_input"] = {"type": "end_session", "payload": {}}
        state["outbox"] = []
        state["turn"] += 1
        state["no_llm"] = True
        state["end_reason"] = (
            "ai_budget_exceeded" if isinstance(exc, AIBudgetExceededError) else "ai_unavailable"
        )
        ai = await self._context(user_id, session_id, "session_end_after_error")
        async with self.sessionmaker() as db:
            state = await self._engine(db, ai, emit).run_turn(state)
            row = await self._row(db, user_id, session_id)
            await self._persist(db, row, state)
        await self.recorder.finish_trace(ai.trace_id, "completed")
        return self._result(session_id, state, [error, *state["outbox"]])

    async def end(
        self, user_id: uuid.UUID, session_id: uuid.UUID, *, emit: Emitter | None = None
    ) -> TurnResult:
        return await self.handle(user_id, session_id, ClientMessage(type="end_session"), emit=emit)

    async def resume(self, user_id: uuid.UUID, session_id: uuid.UUID) -> TurnResult:
        """Where the session stands now — e.g. after a reconnect or a server restart.

        Re-sends what the student was looking at: the open question, or the last
        explanation awaiting acknowledgement.
        """
        async with self.sessionmaker() as db:
            row = await self._row(db, user_id, session_id)
            state = await self.load_state(row)
        if state is None:
            raise ConflictError("The session state could not be recovered")
        events: list[dict[str, Any]] = []
        if state["awaiting"] == "answer" and state.get("current_question"):
            events.append(
                {"type": "question", "payload": public_question(state["current_question"])}
            )
        elif state["awaiting"] == "acknowledgement" and state["last_explanation"]:
            concept = state["current_concept_id"] or ""
            name = state["concept_info"].get(concept, {}).get("name", "")
            events += [
                {
                    "type": "explanation_start",
                    "payload": {"concept_id": concept, "concept_name": name, "kind": "resume"},
                },
                {"type": "explanation_chunk", "payload": {"content": state["last_explanation"]}},
                {"type": "explanation_end", "payload": {}},
            ]
        elif state["awaiting"] == "ended" and state.get("summary"):
            events.append({"type": "session_ended", "payload": {"summary": state["summary"]}})
        return self._result(session_id, state, events)
