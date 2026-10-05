"""Learning sessions over REST, SSE and WebSocket (API_CONTRACT §3.8–3.9, ADR-009).

* REST — create (plans only, fast), list, detail, end, pause, resume, WebSocket ticket.
* WebSocket ``/sessions/{id}/ws?ticket=…`` — the interactive channel: explanation
  chunks stream as they are generated, every turn ends with ``turn_complete``.
* SSE ``POST /sessions/{id}/messages`` (``Accept: text/event-stream``) — the same turn
  as a one-shot event stream; works through the Next.js BFF where WebSockets cannot.

Opening a channel (WebSocket connect, or an SSE ``begin``) starts a planned session,
resumes a paused one, or re-sends the current view of a running one. A dropped
WebSocket pauses the session so paused time doesn't count against the time budget.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.api.deps import (
    CurrentUser,
    DbSession,
    PageParams,
    Sessions,
    get_session_manager,
    rate_limit_sessions,
)
from app.config import Settings
from app.core.exceptions import AppError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger, user_id_ctx
from app.core.middleware import SlidingWindowRateLimiter
from app.db.models import LearningSession, User
from app.db.repositories.session import SessionRepository
from app.domain.common import Envelope, PaginatedEnvelope, envelope, paginated
from app.domain.session import (
    SessionCreated,
    SessionDetail,
    SessionEventRead,
    SessionListItem,
    SessionView,
    TurnRequest,
    WsTicket,
)
from app.services.learning_engine.orchestrator import Emitter
from app.services.learning_engine.session_manager import SessionManager, TurnResult
from app.services.learning_engine.state import ClientMessage, StartSessionRequest
from app.services.learning_engine.tickets import TicketStore

logger = get_logger(__name__)
router = APIRouter(tags=["sessions"])
ws_router = APIRouter(tags=["sessions"])

SessionStatus = Literal["initialising", "active", "paused", "completed", "abandoned"]
WS_POLICY_VIOLATION = 1008


def view_of(result: TurnResult, *, events: bool = True) -> SessionView:
    return SessionView(
        session_id=result.session_id,
        status=result.status,
        awaiting=result.awaiting,
        objective=result.objective,
        current_question=result.current_question,
        current_concept_id=result.current_concept_id,
        interaction_count=result.interaction_count,
        summary=result.summary,
        events=result.events if events else [],
    )


def turn_complete(result: TurnResult) -> dict[str, Any]:
    payload = view_of(result, events=False).model_dump(mode="json", exclude={"events"})
    return {"type": "turn_complete", "payload": payload}


async def open_channel(
    manager: SessionManager, user_id: uuid.UUID, session_id: uuid.UUID, emit: Emitter
) -> TurnResult:
    """Begin, resume or re-send — whatever the session needs when a client starts listening."""
    view = await manager.resume(user_id, session_id)
    if view.status == "initialising":
        return await manager.begin(user_id, session_id, emit=emit)
    if view.status == "paused":
        view = await manager.unpause(user_id, session_id)
    for event in view.events:
        await emit(event)
    return view


def _list_item(row: LearningSession) -> dict[str, Any]:
    targets = (row.objective or {}).get("target_concepts") or []
    return {
        "id": row.id,
        "session_type": row.session_type,
        "status": row.status or "initialising",
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "duration_seconds": row.duration_seconds,
        "interaction_count": row.interaction_count or 0,
        "concepts": [t.get("name", "") for t in targets],
        "summary": row.summary,
    }


async def _owned(db: DbSession, user_id: uuid.UUID, session_id: uuid.UUID) -> LearningSession:
    row = await SessionRepository(db).get_by_id(session_id, user_id)
    if row is None:
        raise NotFoundError("Session not found")
    return row


# --- REST -----------------------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=Envelope[SessionCreated],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_sessions)],
    summary="Start a learning session (plans it; the first activity starts when a channel opens)",
)
async def create_session(
    body: StartSessionRequest, request: Request, user: CurrentUser, manager: Sessions
) -> Envelope[SessionCreated]:
    result = await manager.create(user.id, body)
    settings: Settings = request.app.state.settings
    return envelope(
        request,
        SessionCreated(
            session_id=result.session_id,
            status=result.status,
            websocket_url=f"{settings.api_prefix}/sessions/{result.session_id}/ws",
            objective=result.objective,
        ),
    )


@router.get("/sessions", response_model=PaginatedEnvelope[SessionListItem], summary="List sessions")
async def list_sessions(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    page: PageParams,
    status_: SessionStatus | None = Query(None, alias="status"),
) -> PaginatedEnvelope[SessionListItem]:
    result = await SessionRepository(db).list_sessions(user.id, page, status=status_)
    return paginated(
        request,
        [SessionListItem.model_validate(_list_item(r)) for r in result.items],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
    )


@router.get("/sessions/{session_id}", response_model=Envelope[SessionDetail])
async def get_session(
    session_id: uuid.UUID, request: Request, user: CurrentUser, db: DbSession, manager: Sessions
) -> Envelope[SessionDetail]:
    row = await _owned(db, user.id, session_id)
    events = await SessionRepository(db).events(row.id)
    view = await manager.resume(user.id, session_id)
    detail = SessionDetail.model_validate(
        _list_item(row)
        | {
            "learning_goal_id": row.learning_goal_id,
            "objective": row.objective or {},
            "awaiting": view.awaiting,
            "current_question": view.current_question,
            "events": [
                SessionEventRead(
                    index=e.event_index,
                    type=e.event_type,
                    concept_id=e.concept_id,
                    payload=e.payload or {},
                    created_at=e.created_at,
                )
                for e in events
            ],
        }
    )
    return envelope(request, detail)


@router.post("/sessions/{session_id}/end", response_model=Envelope[SessionView])
async def end_session(
    session_id: uuid.UUID, request: Request, user: CurrentUser, manager: Sessions
) -> Envelope[SessionView]:
    return envelope(request, view_of(await manager.end(user.id, session_id)))


@router.post("/sessions/{session_id}/pause", response_model=Envelope[SessionView])
async def pause_session(
    session_id: uuid.UUID, request: Request, user: CurrentUser, manager: Sessions
) -> Envelope[SessionView]:
    return envelope(request, view_of(await manager.pause(user.id, session_id)))


@router.post("/sessions/{session_id}/resume", response_model=Envelope[SessionView])
async def resume_session(
    session_id: uuid.UUID, request: Request, user: CurrentUser, manager: Sessions
) -> Envelope[SessionView]:
    return envelope(request, view_of(await manager.unpause(user.id, session_id)))


@router.post("/sessions/{session_id}/ws-ticket", response_model=Envelope[WsTicket])
async def issue_ticket(
    session_id: uuid.UUID, request: Request, user: CurrentUser, db: DbSession
) -> Envelope[WsTicket]:
    row = await _owned(db, user.id, session_id)
    if row.status in ("completed", "abandoned"):
        raise ConflictError("This session has ended", details={"status": row.status})
    settings: Settings = request.app.state.settings
    ticket = await TicketStore(request.app.state.redis, settings.ws_ticket_ttl_seconds).issue(
        user.id, session_id
    )
    return envelope(
        request,
        WsTicket(
            ticket=ticket.token,
            expires_in_seconds=ticket.expires_in_seconds,
            websocket_path=f"{settings.api_prefix}/sessions/{session_id}/ws",
        ),
    )


# --- SSE ---------------------------------------------------------------------------------


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event['payload'], default=str)}\n\n"


@router.post(
    "/sessions/{session_id}/messages",
    response_model=Envelope[SessionView],
    summary="Send a message (or `begin`); `Accept: text/event-stream` streams the turn",
)
async def post_message(
    session_id: uuid.UUID, body: TurnRequest, request: Request, user: CurrentUser, manager: Sessions
) -> Any:
    try:
        msg = body.client_message()
    except PydanticValidationError as exc:
        raise ValidationError(
            "Invalid message",
            details={
                "errors": [
                    {"loc": list(e["loc"]), "message": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]
            },
        ) from exc
    user_id = user.id

    async def run(emit: Emitter) -> TurnResult:
        if msg is None:
            return await open_channel(manager, user_id, session_id, emit)
        return await manager.handle(user_id, session_id, msg, emit=emit)

    if "text/event-stream" not in request.headers.get("accept", ""):
        collected: list[dict[str, Any]] = []

        async def collect(event: dict[str, Any]) -> None:
            if (
                event["type"] != "explanation_chunk"
                or not collected
                or collected[-1]["type"] != "explanation_chunk"
            ):
                collected.append(event)
            else:  # coalesce streamed chunks
                collected[-1] = {
                    "type": "explanation_chunk",
                    "payload": {
                        "content": collected[-1]["payload"]["content"] + event["payload"]["content"]
                    },
                }

        result = await run(collect)
        return envelope(request, view_of(result).model_copy(update={"events": collected}))

    # Validate ownership/state up front so errors are proper HTTP responses, not stream events.
    await manager.resume(user_id, session_id)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def produce() -> None:
        try:
            result = await run(queue.put)
            await queue.put(turn_complete(result))
        except AppError as exc:
            await queue.put(
                {
                    "type": "error",
                    "payload": {"code": exc.code, "message": exc.message, **exc.details},
                }
            )
        except Exception:  # pragma: no cover - logged, reported to the client
            logger.exception("session stream failed", extra={"session_id": str(session_id)})
            await queue.put(
                {
                    "type": "error",
                    "payload": {"code": "INTERNAL_ERROR", "message": "Unexpected error"},
                }
            )
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(produce())
        try:
            while (event := await queue.get()) is not None:
                yield _sse(event)
        finally:
            # A client that disconnects mid-turn must not lose the turn: let it finish and persist.
            await asyncio.shield(task)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- WebSocket ------------------------------------------------------------------------------


@ws_router.websocket("/sessions/{session_id}/ws")
async def session_socket(websocket: WebSocket, session_id: uuid.UUID, ticket: str = "") -> None:
    app = websocket.app
    settings: Settings = app.state.settings
    origin = websocket.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in settings.cors_origins:
        await websocket.close(code=WS_POLICY_VIOLATION)  # cross-site WebSocket hijacking guard
        return
    user_id = await TicketStore(app.state.redis, settings.ws_ticket_ttl_seconds).redeem(
        ticket, session_id
    )
    if user_id is None:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return
    async with app.state.sessionmaker() as db:
        active = await db.scalar(select(User.is_active).where(User.id == user_id))
    if active is False or active is None:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return

    await websocket.accept()
    user_id_ctx.set(str(user_id))
    manager = get_session_manager(websocket)  # type: ignore[arg-type]
    limiter = SlidingWindowRateLimiter(app.state.redis, 60)

    async def send(event: dict[str, Any]) -> None:
        await websocket.send_json(event)

    async def send_error(code: str, message: str, **details: Any) -> None:
        await send({"type": "error", "payload": {"code": code, "message": message, **details}})

    ended = False
    try:
        try:
            result = await open_channel(manager, user_id, session_id, send)
        except AppError as exc:
            await send_error(exc.code, exc.message, **exc.details)
            await websocket.close(code=WS_POLICY_VIOLATION if exc.status_code == 404 else 1000)
            return
        await send(turn_complete(result))
        if result.status in ("completed", "abandoned"):
            ended = True
            await websocket.close(code=1000)
            return

        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except ValueError:
                await send_error("VALIDATION_ERROR", "Messages must be JSON")
                continue
            if isinstance(data, dict) and data.get("type") == "ping":
                await send({"type": "pong", "payload": {}})
                continue
            if settings.rate_limit_enabled:
                try:
                    hit = await limiter.hit(
                        str(user_id), "ws", settings.rate_limit_ws_messages_per_minute
                    )
                except Exception:  # availability over strictness, as for HTTP
                    hit = None
                if hit is not None and not hit.allowed:
                    await send_error(
                        "RATE_LIMITED", "Too many messages; slow down a little", retry_after=60
                    )
                    continue
            try:
                msg = ClientMessage.model_validate(data)
            except PydanticValidationError as exc:
                await send_error(
                    "VALIDATION_ERROR",
                    "Invalid message",
                    errors=[{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()],
                )
                continue
            try:
                result = await manager.handle(user_id, session_id, msg, emit=send)
            except AppError as exc:
                await send_error(exc.code, exc.message, **exc.details)
                continue
            await send(turn_complete(result))
            if result.status in ("completed", "abandoned"):
                ended = True
                await websocket.close(code=1000)
                return
    except WebSocketDisconnect:
        pass
    finally:
        if not ended:
            try:
                await manager.pause(user_id, session_id)
            except AppError:
                pass  # ended or gone — nothing to pause
            except Exception:  # pragma: no cover - best effort on disconnect
                logger.warning(
                    "could not pause session on disconnect", extra={"session_id": str(session_id)}
                )
