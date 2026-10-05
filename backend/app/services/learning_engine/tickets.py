"""Single-use WebSocket tickets (SECURITY_MODEL §2, Phase 2 BFF design).

Browsers cannot send an ``Authorization`` header on a WebSocket, and in this app the
browser never holds the Supabase JWT at all (httpOnly cookies + the Next.js BFF). So
the BFF — authenticated with the JWT — asks for a ticket, and the browser opens the
socket with it. A ticket:

* is a 256-bit random token, stored only in Redis (``ws_ticket:{token}``);
* is bound to one user *and* one session;
* expires after ``ws_ticket_ttl_seconds`` (60 s) and can be redeemed exactly once
  (atomic ``GETDEL``), so a leaked URL is useless.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

KEY_PREFIX = "ws_ticket:"


@dataclass(frozen=True, slots=True)
class Ticket:
    token: str
    expires_in_seconds: int


class TicketStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 60) -> None:
        self.redis = redis
        self.ttl = ttl_seconds

    async def issue(self, user_id: uuid.UUID, session_id: uuid.UUID) -> Ticket:
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"user_id": str(user_id), "session_id": str(session_id)})
        await self.redis.set(KEY_PREFIX + token, payload, ex=self.ttl)
        return Ticket(token=token, expires_in_seconds=self.ttl)

    async def redeem(self, token: str, session_id: uuid.UUID) -> uuid.UUID | None:
        """The ticket's user if it is valid for this session; consumed either way."""
        if not token or len(token) > 128:
            return None
        raw = await self.redis.getdel(KEY_PREFIX + token)
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("session_id") != str(session_id):
            return None
        return uuid.UUID(data["user_id"])
