"""HTTP middleware: request IDs + access logging, security headers, CORS, and the
Redis sliding-window rate limiter.

Rate limits are per *user*, so `SlidingWindowRateLimiter` is applied by a
dependency after authentication (see `app.api.deps`), not as ASGI middleware.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.core.logging import get_logger, request_id_ctx, user_id_ctx

logger = get_logger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
# Interactive API docs load Swagger/ReDoc assets from a CDN; skip the strict CSP there.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class RequestContextMiddleware:
    """Assigns a correlation ID to every request and emits one access-log line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.lower().encode(), b"").decode()
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        rid_token = request_id_ctx.set(request_id)
        uid_token = user_id_ctx.set(None)
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            request_id_ctx.reset(rid_token)
            user_id_ctx.reset(uid_token)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"].startswith(_DOCS_PATHS)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if is_docs and name == "Content-Security-Policy":
                        continue
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # unix epoch seconds when the oldest counted hit leaves the window

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_at),
        }


class SlidingWindowRateLimiter:
    """Sliding-window log limiter on a Redis sorted set (key ``rate:{subject}:{scope}``)."""

    def __init__(self, redis: Redis, window_seconds: int = 60) -> None:
        self.redis = redis
        self.window = window_seconds

    async def hit(self, subject: str, scope: str, limit: int) -> RateLimitResult:
        key = f"rate:{subject}:{scope}"
        now = time.time()
        member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - self.window)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.zrange(key, 0, 0, withscores=True)
            pipe.expire(key, self.window)
            _, _, count, oldest, _ = await pipe.execute()

        allowed = count <= limit
        if not allowed:
            # Rejected requests don't consume capacity.
            await self.redis.zrem(key, member)
        oldest_ts = oldest[0][1] if oldest else now
        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=max(0, limit - min(count, limit)),
            reset_at=math.ceil(oldest_ts + self.window),
        )


def register_middleware(app: FastAPI, settings: Settings) -> None:
    # Starlette wraps in reverse order of registration: the last added is outermost.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[
            REQUEST_ID_HEADER,
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
    )
    app.add_middleware(RequestContextMiddleware)
