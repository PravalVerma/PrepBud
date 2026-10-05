"""AI observability: traces, per-call interaction logs, cost and budget (AI_SYSTEM_DESIGN
§2.3, §9; SECURITY_MODEL §6.3, §7.3).

Every LLM / embedding call becomes an immutable `AIInteraction` row grouped under an
`AITrace`. Rows are written in their own short transactions so the audit trail
survives even when the caller's work later rolls back or fails.

The per-user daily spend is mirrored in Redis (``cost:{user_id}:{date}``, 25 h TTL)
for a cheap budget check before each call; if Redis is unavailable the check falls
back to summing today's `ai_interactions`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.providers.base import AIBudgetExceededError
from app.config import ModelPricing, Settings
from app.core.logging import get_logger
from app.db.models import AIInteraction, AITrace

logger = get_logger(__name__)

COST_KEY_TTL_SECONDS = 25 * 3600
_UNPRICED_WARNED: set[str] = set()


def compute_cost(
    pricing: dict[str, ModelPricing], model: str, input_tokens: int, output_tokens: int
) -> float | None:
    """USD cost for one call, or ``None`` when the model has no configured price.

    Providers often report dated variants (``gpt-4o-mini-2024-07-18``); the longest
    configured model name that prefixes the reported one is used.
    """
    price = pricing.get(model)
    if price is None:
        candidates = [name for name in pricing if model.startswith(name)]
        if candidates:
            price = pricing[max(candidates, key=len)]
    if price is None:
        return None
    return (input_tokens * price.input_per_1m + output_tokens * price.output_per_1m) / 1_000_000


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cost_key(user_id: uuid.UUID, day: datetime | None = None) -> str:
    day = day or datetime.now(UTC)
    return f"cost:{user_id}:{day.date().isoformat()}"


@dataclass(slots=True)
class AICallContext:
    """Who is calling and why — attached to every AIInteraction."""

    user_id: uuid.UUID
    trace_id: uuid.UUID
    purpose: str
    prompt_name: str | None = None
    prompt_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def for_purpose(
        self, purpose: str, *, prompt_name: str | None = None, prompt_version: str | None = None
    ) -> AICallContext:
        return AICallContext(
            user_id=self.user_id,
            trace_id=self.trace_id,
            purpose=purpose,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class InteractionRecord:
    provider: str
    model: str
    task: str
    request: Any
    response: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    status: Literal["success", "error", "timeout"] = "success"
    error_message: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AIUsageRecorder:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis | None = None,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.redis = redis

    # --- traces ------------------------------------------------------------------------

    async def start_trace(
        self,
        user_id: uuid.UUID,
        operation: str,
        *,
        session_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        async with self.sessionmaker() as session:
            trace = AITrace(
                user_id=user_id,
                session_id=session_id,
                operation=operation,
                status="started",
                metadata_=metadata or {},
            )
            session.add(trace)
            await session.commit()
            return trace.id

    async def finish_trace(
        self, trace_id: uuid.UUID, status: Literal["completed", "failed"] = "completed"
    ) -> None:
        async with self.sessionmaker() as session:
            await session.execute(
                update(AITrace)
                .where(AITrace.id == trace_id)
                .values(status=status, completed_at=func.now())
            )
            await session.commit()

    # --- interactions -------------------------------------------------------------------

    async def record(self, ctx: AICallContext, rec: InteractionRecord) -> float:
        """Persist one interaction; returns its cost estimate (USD)."""
        cost = compute_cost(
            self.settings.ai_pricing, rec.model, rec.input_tokens, rec.output_tokens
        )
        request_text = (
            rec.request if isinstance(rec.request, str) else json.dumps(rec.request, default=str)
        )
        metadata: dict[str, Any] = {
            "task": rec.task,
            "prompt_name": ctx.prompt_name,
            "prompt_version": ctx.prompt_version,
            **ctx.metadata,
            **rec.extra,
        }
        if cost is None:
            metadata["cost_unknown"] = True
            if rec.model not in _UNPRICED_WARNED:  # once per model, not once per call
                _UNPRICED_WARNED.add(rec.model)
                logger.warning("no pricing configured for model", extra={"model": rec.model})
        if self.settings.ai_log_content:
            metadata["request"] = rec.request
            metadata["response"] = rec.response

        total_tokens = rec.input_tokens + rec.output_tokens
        async with self.sessionmaker() as session:
            session.add(
                AIInteraction(
                    trace_id=ctx.trace_id,
                    user_id=ctx.user_id,
                    purpose=ctx.purpose,
                    provider=rec.provider,
                    model=rec.model,
                    prompt_hash=sha256(request_text),
                    response_hash=sha256(rec.response) if rec.response is not None else None,
                    input_tokens=rec.input_tokens,
                    output_tokens=rec.output_tokens,
                    cost_estimate=cost or 0.0,
                    latency_ms=rec.latency_ms,
                    status=rec.status,
                    error_message=rec.error_message,
                    metadata_=metadata,
                )
            )
            await session.execute(
                update(AITrace)
                .where(AITrace.id == ctx.trace_id)
                .values(
                    total_tokens=func.coalesce(AITrace.total_tokens, 0) + total_tokens,
                    total_cost=func.coalesce(AITrace.total_cost, 0.0) + (cost or 0.0),
                )
            )
            await session.commit()

        if cost and self.redis is not None:
            try:
                key = cost_key(ctx.user_id)
                async with self.redis.pipeline(transaction=True) as pipe:
                    pipe.incrbyfloat(key, cost)
                    pipe.expire(key, COST_KEY_TTL_SECONDS)
                    await pipe.execute()
            except Exception as exc:  # the DB row is the source of truth
                logger.warning("cost counter unavailable", extra={"error": type(exc).__name__})
        return cost or 0.0

    # --- budget -----------------------------------------------------------------------

    async def spent_today(self, user_id: uuid.UUID) -> float:
        if self.redis is not None:
            try:
                value = await self.redis.get(cost_key(user_id))
                return float(value) if value is not None else 0.0
            except Exception as exc:
                logger.warning("cost counter unavailable", extra={"error": type(exc).__name__})
        start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
        async with self.sessionmaker() as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(AIInteraction.cost_estimate), 0.0)).where(
                    AIInteraction.user_id == user_id,
                    AIInteraction.created_at >= start,
                    AIInteraction.created_at < start + timedelta(days=1),
                )
            )
        return float(total or 0.0)

    async def check_budget(self, user_id: uuid.UUID) -> None:
        budget = self.settings.ai_daily_budget_usd
        if budget <= 0:
            return
        spent = await self.spent_today(user_id)
        if spent >= budget:
            raise AIBudgetExceededError(
                f"Daily AI budget of ${budget:.2f} reached; try again tomorrow"
            )
