"""Sessions over REST, WebSocket and SSE (API_CONTRACT §3.8–3.9; AC-5.1, 5.2, 5.4, 5.5, 5.7)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import LLMServerError
from app.db.models import Concept, LearningSession, SessionEvent
from tests.fakes import FakeLLMProvider
from tests.integration.conftest import provision
from tests.support import auth

API = "/api/v1"
H = auth("ws-learner")
ORIGIN = {"origin": "http://localhost:3000"}


@asynccontextmanager
async def socket(app: FastAPI, url: str, headers: dict[str, str] = ORIGIN) -> AsyncIterator[Any]:
    """A WebSocket on its own in-process transport.

    Entered inside the test (not a fixture) so the transport's task group starts and stops in one
    task; leaving it waits for the server side to finish, including pause-on-disconnect.
    """
    transport = ASGIWebSocketTransport(app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as c,
        aconnect_ws(url, c, headers=headers) as ws,
    ):
        yield ws


async def rejected(app: FastAPI, url: str, headers: dict[str, str] = ORIGIN) -> None:
    """The server closes the handshake with 1008 (policy violation) instead of accepting."""
    policy = pytest.RaisesExc(WebSocketDisconnect, check=lambda e: e.code == 1008)
    with pytest.RaisesGroup(policy):  # the transport's task group wraps it
        async with socket(app, url, headers):
            pass  # pragma: no cover


@pytest.fixture
async def concepts(
    client: httpx.AsyncClient, db: AsyncSession, fake_llm: FakeLLMProvider
) -> list[Concept]:
    fake_llm.stream_text = "Here is a clear explanation of the idea."
    user_id = await provision(client, H)
    items = [
        Concept(
            user_id=user_id, name="Limits", description="Values approached", difficulty_estimate=0.3
        ),
        Concept(
            user_id=user_id,
            name="Vectors",
            description="Magnitude and direction",
            difficulty_estimate=0.6,
        ),
    ]
    db.add_all(items)
    await db.commit()
    return items


async def create(
    client: httpx.AsyncClient, body: dict[str, Any] | None = None, headers: dict[str, str] = H
) -> dict[str, Any]:
    resp = await client.post(
        f"{API}/sessions", json=body or {"session_type": "teach"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


async def ticket(client: httpx.AsyncClient, session_id: str, headers: dict[str, str] = H) -> str:
    resp = await client.post(f"{API}/sessions/{session_id}/ws-ticket", headers=headers)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["ticket"])


async def until(ws: Any, kind: str = "turn_complete") -> list[dict[str, Any]]:
    """Receive events up to and including the first of ``kind``."""
    events = []
    while True:
        event = await ws.receive_json()
        events.append(event)
        if event["type"] == kind:
            return events


def payload(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(e["payload"] for e in events if e["type"] == kind)


class TestRest:
    async def test_create_plans_without_teaching(
        self, client: httpx.AsyncClient, concepts: list[Concept]
    ) -> None:
        data = await create(client, {"session_type": "mixed", "time_budget_minutes": 20})
        assert data["status"] == "initialising"
        assert data["websocket_url"] == f"/api/v1/sessions/{data['session_id']}/ws"
        assert [t["name"] for t in data["objective"]["target_concepts"]] == ["Limits", "Vectors"]
        assert data["objective"]["session_type"] == "mixed"
        assert all(t["mastery"] == 0.0 for t in data["objective"]["target_concepts"])  # AC-5.4

    async def test_create_validation_and_errors(
        self, client: httpx.AsyncClient, concepts: list[Concept]
    ) -> None:
        bad = await client.post(f"{API}/sessions", json={"session_type": "nap"}, headers=H)
        assert bad.status_code == 400
        missing = await client.post(
            f"{API}/sessions", json={"learning_goal_id": str(uuid.uuid4())}, headers=H
        )
        assert missing.status_code == 404
        empty = await client.post(f"{API}/sessions", json={}, headers=auth("no-concepts"))
        assert (
            empty.status_code == 422 and empty.json()["error"]["details"]["reason"] == "NO_CONCEPTS"
        )

    async def test_list_detail_and_isolation(
        self, client: httpx.AsyncClient, concepts: list[Concept]
    ) -> None:
        first = await create(client)
        await create(client, {"session_type": "practice"})
        listed = (await client.get(f"{API}/sessions", headers=H)).json()
        assert listed["meta"]["pagination"]["total"] == 2
        assert listed["data"][0]["session_type"] == "practice"  # newest first
        assert listed["data"][1]["concepts"] == ["Limits", "Vectors"]
        filtered = (await client.get(f"{API}/sessions?status=completed", headers=H)).json()["data"]
        assert filtered == []
        assert (await client.get(f"{API}/sessions?status=sleeping", headers=H)).status_code == 400

        detail = (await client.get(f"{API}/sessions/{first['session_id']}", headers=H)).json()[
            "data"
        ]
        assert detail["status"] == "initialising" and detail["awaiting"] == "none"
        assert [e["type"] for e in detail["events"]] == ["concept_changed"]

        other = auth("someone-else")
        for method, path in [
            ("GET", f"/sessions/{first['session_id']}"),
            ("POST", f"/sessions/{first['session_id']}/ws-ticket"),
            ("POST", f"/sessions/{first['session_id']}/end"),
            ("POST", f"/sessions/{first['session_id']}/pause"),
            ("POST", f"/sessions/{first['session_id']}/messages"),
        ]:
            body = {"type": "begin"} if path.endswith("messages") else None
            resp = await client.request(method, API + path, json=body, headers=other)
            assert resp.status_code == 404, path
        assert (await client.get(f"{API}/sessions", headers=other)).json()["data"] == []

    async def test_session_start_rate_limit(
        self, app: FastAPI, client: httpx.AsyncClient, concepts: list[Concept]
    ) -> None:
        app.state.settings.rate_limit_sessions_per_hour = 1
        await create(client)
        assert (await client.post(f"{API}/sessions", json={}, headers=H)).status_code == 429

    async def test_end_pause_resume(
        self, client: httpx.AsyncClient, concepts: list[Concept], db: AsyncSession
    ) -> None:
        sid = (await create(client, {"session_type": "practice"}))["session_id"]
        begun = (
            await client.post(f"{API}/sessions/{sid}/messages", json={"type": "begin"}, headers=H)
        ).json()["data"]
        assert begun["awaiting"] == "answer" and begun["current_question"]

        paused = (await client.post(f"{API}/sessions/{sid}/pause", headers=H)).json()["data"]
        assert paused["status"] == "paused"
        again = (await client.post(f"{API}/sessions/{sid}/pause", headers=H)).json()["data"]
        assert again["status"] == "paused"  # idempotent
        resumed = (await client.post(f"{API}/sessions/{sid}/resume", headers=H)).json()["data"]
        assert resumed["status"] == "active"
        assert resumed["events"][0]["type"] == "question"  # what the student was looking at

        ended = (await client.post(f"{API}/sessions/{sid}/end", headers=H)).json()["data"]
        assert ended["status"] == "completed" and ended["summary"]["end_reason"] == "student_ended"
        assert [e["type"] for e in ended["events"]][-1] == "session_ended"
        conflict = await client.post(f"{API}/sessions/{sid}/ws-ticket", headers=H)
        assert conflict.status_code == 409
        logged = set((await db.scalars(select(SessionEvent.event_type))).all())
        assert {"session_paused", "session_resumed"} <= logged


class TestWebSocket:
    async def test_full_journey(
        self, app: FastAPI, client: httpx.AsyncClient, concepts: list[Concept], db: AsyncSession
    ) -> None:
        sid = (
            await create(client, {"session_type": "teach", "concept_ids": [str(concepts[0].id)]})
        )["session_id"]
        url = f"{API}/sessions/{sid}/ws?ticket={await ticket(client, sid)}"
        async with socket(app, url) as ws:
            # AC-5.1: the explanation streams in many chunks, then the turn completes.
            opening = await until(ws)
            kinds = [e["type"] for e in opening]
            assert kinds[0] == "explanation_start" and kinds[-2:] == [
                "explanation_end",
                "turn_complete",
            ]
            chunks = [e["payload"]["content"] for e in opening if e["type"] == "explanation_chunk"]
            assert (
                len(chunks) > 3
                and "".join(chunks).strip() == "Here is a clear explanation of the idea."
            )
            assert payload(opening, "turn_complete")["awaiting"] == "acknowledgement"

            await ws.send_json({"type": "student_acknowledge", "payload": {"understood": True}})
            turn = await until(ws)
            question = payload(turn, "question")
            assert payload(turn, "turn_complete")["awaiting"] == "answer"
            assert question["type"] == "true_false" and "correct_answer" not in question

            # AC-5.2 / AC-5.4: immediate evaluation with the mastery change.
            await ws.send_json(
                {
                    "type": "student_response",
                    "payload": {
                        "content": "true",
                        "question_id": question["question_id"],
                        "time_taken_seconds": 8,
                    },
                }
            )
            turn = await until(ws)
            evaluation = payload(turn, "evaluation")
            assert evaluation["is_correct"]
            assert (
                evaluation["mastery_update"]["new_mastery"]
                > evaluation["mastery_update"]["old_mastery"]
            )
            assert payload(turn, "turn_complete")["interaction_count"] == 2

            await ws.send_json({"type": "request_hint"})
            assert payload(await until(ws), "hint")["hint_number"] == 1
            await ws.send_json({"type": "student_question", "payload": {"content": "Why is that?"}})
            follow = await until(ws)
            assert payload(follow, "explanation_start")["kind"] == "followup"

            # AC-5.5: ending shows the summary, then the server closes the socket.
            await ws.send_json({"type": "end_session", "payload": {}})
            final = await until(ws)
            summary = payload(final, "session_ended")["summary"]
            assert summary["questions_answered"] == 1 and summary["text"]
            assert payload(final, "turn_complete")["status"] == "completed"
        row = await db.scalar(select(LearningSession).where(LearningSession.id == uuid.UUID(sid)))
        assert row is not None and row.status == "completed"

    async def test_bad_messages_keep_the_connection(
        self, app: FastAPI, client: httpx.AsyncClient, concepts: list[Concept]
    ) -> None:
        app.state.settings.rate_limit_ws_messages_per_minute = 3  # ping/garbage not counted
        sid = (await create(client))["session_id"]
        async with socket(app, f"{API}/sessions/{sid}/ws?ticket={await ticket(client, sid)}") as ws:
            await until(ws)
            await ws.send_text("not json")
            assert (await ws.receive_json())["payload"]["code"] == "VALIDATION_ERROR"
            await ws.send_json({"type": "ping"})
            assert (await ws.receive_json())["type"] == "pong"
            await ws.send_json({"type": "dance"})
            assert (await ws.receive_json())["payload"]["code"] == "VALIDATION_ERROR"
            await ws.send_json(
                {"type": "request_hint"}
            )  # nothing to hint: awaiting acknowledgement
            conflict = await ws.receive_json()
            assert (
                conflict["payload"]["code"] == "CONFLICT"
                and conflict["payload"]["awaiting"] == "acknowledgement"
            )
            await ws.send_json({"type": "request_hint"})
            await ws.receive_json()
            await ws.send_json({"type": "request_hint"})
            assert (await ws.receive_json())["payload"]["code"] == "RATE_LIMITED"

    async def test_ticket_and_origin_checks(
        self, app: FastAPI, client: httpx.AsyncClient, concepts: list[Concept]
    ) -> None:
        sid = (await create(client))["session_id"]
        other_sid = (await create(client))["session_id"]
        base = f"{API}/sessions/{sid}/ws"
        for url, headers in [
            (base, ORIGIN),  # no ticket
            (f"{base}?ticket=forged", ORIGIN),
            (
                f"{base}?ticket={await ticket(client, other_sid)}",
                ORIGIN,
            ),  # ticket for another session
            (f"{base}?ticket={await ticket(client, sid)}", {"origin": "https://evil.example"}),
        ]:
            await rejected(app, url, headers)
        used = await ticket(client, sid)
        async with socket(app, f"{base}?ticket={used}") as ws:
            await until(ws)
        await rejected(app, f"{base}?ticket={used}")  # single use

    async def test_disconnect_pauses_and_reconnect_resumes(
        self, app: FastAPI, client: httpx.AsyncClient, concepts: list[Concept], db: AsyncSession
    ) -> None:
        sid = (await create(client, {"session_type": "practice"}))["session_id"]
        async with socket(app, f"{API}/sessions/{sid}/ws?ticket={await ticket(client, sid)}") as ws:
            first = await until(ws)
        question = payload(first, "question")
        detail = (await client.get(f"{API}/sessions/{sid}", headers=H)).json()["data"]
        assert detail["status"] == "paused"

        async with socket(app, f"{API}/sessions/{sid}/ws?ticket={await ticket(client, sid)}") as ws:
            again = await until(ws)
            assert payload(again, "question")["question_id"] == question["question_id"]
            assert payload(again, "turn_complete")["status"] == "active"
            await ws.send_json({"type": "end_session", "payload": {}})
            await until(ws)
        assert (await client.get(f"{API}/sessions/{sid}", headers=H)).json()["data"][
            "status"
        ] == "completed"

    async def test_llm_outage_is_reported_and_retryable(
        self,
        app: FastAPI,
        client: httpx.AsyncClient,
        concepts: list[Concept],
        fake_llm: FakeLLMProvider,
    ) -> None:
        """AC-5.7."""
        sid = (await create(client))["session_id"]
        async with socket(app, f"{API}/sessions/{sid}/ws?ticket={await ticket(client, sid)}") as ws:
            await until(ws)
            fake_llm.fail_kinds = {"question": LLMServerError("down")}
            await ws.send_json({"type": "student_acknowledge", "payload": {"understood": True}})
            failed = await until(ws)
            error = payload(failed, "error")
            assert (
                error["code"] == "LLM_UNAVAILABLE"
                and error["retryable"] is True
                and error["message"]
            )
            assert payload(failed, "turn_complete")["awaiting"] == "acknowledgement"
            fake_llm.fail_kinds = {}
            await ws.send_json({"type": "student_acknowledge", "payload": {"understood": True}})
            assert payload(await until(ws), "turn_complete")["awaiting"] == "answer"

    async def test_ended_session_sends_summary_and_closes(
        self, app: FastAPI, client: httpx.AsyncClient, concepts: list[Concept]
    ) -> None:
        sid = (await create(client))["session_id"]
        fresh = await ticket(client, sid)
        await client.post(f"{API}/sessions/{sid}/end", headers=H)
        async with socket(app, f"{API}/sessions/{sid}/ws?ticket={fresh}") as ws:
            events = await until(ws)
            assert [e["type"] for e in events] == ["session_ended", "turn_complete"]


class TestSse:
    async def test_streams_a_turn(self, client: httpx.AsyncClient, concepts: list[Concept]) -> None:
        sid = (await create(client))["session_id"]
        async with client.stream(
            "POST",
            f"{API}/sessions/{sid}/messages",
            json={"type": "begin"},
            headers={**H, "accept": "text/event-stream"},
        ) as resp:
            assert resp.status_code == 200 and resp.headers["content-type"].startswith(
                "text/event-stream"
            )
            body = (await resp.aread()).decode()
        blocks = [b for b in body.split("\n\n") if b.strip()]
        names = [b.split("\n")[0].removeprefix("event: ") for b in blocks]
        assert names[0] == "explanation_start" and names[-1] == "turn_complete"
        assert names.count("explanation_chunk") > 3
        final = json.loads(blocks[-1].split("\n")[1].removeprefix("data: "))
        assert final["awaiting"] == "acknowledgement"

    async def test_json_mode_coalesces_chunks(
        self, client: httpx.AsyncClient, concepts: list[Concept]
    ) -> None:
        sid = (await create(client))["session_id"]
        data = (
            await client.post(f"{API}/sessions/{sid}/messages", json={"type": "begin"}, headers=H)
        ).json()["data"]
        assert [e["type"] for e in data["events"]] == [
            "explanation_start",
            "explanation_chunk",
            "explanation_end",
        ]
        assert (
            data["events"][1]["payload"]["content"] == "Here is a clear explanation of the idea. "
        )

    async def test_errors(self, client: httpx.AsyncClient, concepts: list[Concept]) -> None:
        sid = (await create(client))["session_id"]
        bad = await client.post(
            f"{API}/sessions/{sid}/messages",
            json={"type": "student_response", "payload": {}},
            headers=H,
        )
        assert bad.status_code == 400 and bad.json()["error"]["details"]["errors"]
        early = await client.post(
            f"{API}/sessions/{sid}/messages",
            json={"type": "request_hint", "payload": {}},
            headers=H,
        )
        assert early.status_code == 409 and early.json()["error"]["details"]["awaiting"] == "begin"
        stream_error = await client.post(
            f"{API}/sessions/{uuid.uuid4()}/messages",
            json={"type": "begin"},
            headers={**H, "accept": "text/event-stream"},
        )
        assert stream_error.status_code == 404
