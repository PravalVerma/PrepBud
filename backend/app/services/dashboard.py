"""Mastery dashboard, misconceptions and AI usage (API_CONTRACT §3.11–3.13).

Aggregates are cached per user in Redis for a minute (``dash:{user}:v{n}:…``). Anything that
changes them — a completed session, a processed document, the daily decay job — bumps the
user's version counter (`invalidate_dashboard`), so the student never sees stale numbers
after studying; the TTL only bounds staleness for rarer changes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.db.repositories.dashboard import DashboardRepository
from app.domain.common import mastery_label

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 60
STRUGGLING_BELOW = 0.4
LABELS = ("novice", "beginner", "intermediate", "proficient", "mastered")
Period = Literal["today", "week", "month"]
PERIOD_DAYS: dict[str, int] = {"today": 0, "week": 6, "month": 29}


def _version_key(user_id: uuid.UUID) -> str:
    return f"dash:{user_id}:version"


async def invalidate_dashboard(redis: Redis | None, user_id: uuid.UUID) -> None:
    """Make the next dashboard read recompute (best effort)."""
    if redis is None:
        return
    try:
        await redis.incr(_version_key(user_id))
    except Exception as exc:
        logger.warning("dashboard cache invalidation failed", extra={"error": type(exc).__name__})


def streak_days(active: set[date], today: date) -> int:
    """Consecutive active days ending today — or yesterday, so the streak survives until
    the student has had a chance to study today."""
    day = today if today in active else today - timedelta(days=1)
    count = 0
    while day in active:
        count += 1
        day -= timedelta(days=1)
    return count


def week_columns(today: date, weeks: int) -> list[date]:
    return [today - timedelta(days=7 * (weeks - 1 - i)) for i in range(weeks)]


def mastery_at(
    history: list[Any], day: date, *, fallback: float | None, assessed: date | None
) -> float | None:
    """Mastery as of the end of ``day`` from the history log (last entry on or before it)."""
    value: float | None = None
    for entry in history:
        if not isinstance(entry, dict):
            continue
        try:
            when = date.fromisoformat(str(entry.get("date")))
            level = float(entry.get("mastery"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if when <= day:
            value = level  # entries are appended chronologically
    if value is None and fallback is not None and assessed is not None and assessed <= day:
        value = fallback
    return None if value is None else round(value, 4)


class DashboardService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        redis: Redis | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = DashboardRepository(session)
        self.settings = settings
        self.cfg = settings.learning_engine
        self.redis = redis
        self.now = now

    # ---- caching ------------------------------------------------------------------------

    async def _cached(
        self,
        user_id: uuid.UUID,
        name: str,
        build: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        if self.redis is None:
            return await build()
        try:
            version = await self.redis.get(_version_key(user_id)) or "0"
            key = f"dash:{user_id}:v{version}:{name}"
            hit = await self.redis.get(key)
            if hit:
                cached: dict[str, Any] = json.loads(hit)
                return cached
        except Exception as exc:  # cache trouble never breaks the dashboard
            logger.warning("dashboard cache unavailable", extra={"error": type(exc).__name__})
            return await build()
        value = await build()
        try:
            await self.redis.set(key, json.dumps(value, default=str), ex=CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.warning("dashboard cache write failed", extra={"error": type(exc).__name__})
        return value

    async def _today(self, user_id: uuid.UUID) -> tuple[str, date]:
        tz = await self.repo.timezone(user_id)
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            tz, zone = "UTC", ZoneInfo("UTC")
        return tz, self.now().astimezone(zone).date()

    # ---- overview -----------------------------------------------------------------------

    async def overview(self, user_id: uuid.UUID, *, days: int = 30) -> dict[str, Any]:
        return await self._cached(
            user_id, f"overview:{days}", lambda: self._build_overview(user_id, days)
        )

    async def _build_overview(self, user_id: uuid.UUID, days: int) -> dict[str, Any]:
        learned = self.cfg.mastery_learned_threshold
        tz, today = await self._today(user_id)
        subjects = await self.repo.subject_mastery(
            user_id, learned=learned, struggling_below=STRUGGLING_BELOW
        )
        levels = await self.repo.mastery_distribution(user_id)
        distribution = dict.fromkeys(LABELS, 0)
        for level in levels:
            distribution[mastery_label(level)] += 1
        since = self.now() - timedelta(days=days + 1)
        activity = await self.repo.daily_activity(user_id, tz, since)
        active = await self.repo.active_days(user_id, tz, self.now() - timedelta(days=400))
        totals = await self.repo.totals(user_id)
        answered = totals["questions_answered"]
        series = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            a = activity.get(day)
            series.append(
                {
                    "date": day.isoformat(),
                    "sessions": a.sessions if a else 0,
                    "minutes": a.minutes if a else 0.0,
                    "questions": a.questions if a else 0,
                    "concepts_practiced": a.concepts_practiced if a else 0,
                }
            )
        mastered = sum(1 for v in levels if v >= learned)
        return {
            "subjects": [
                {
                    "id": s.id,
                    "name": s.name,
                    "avg_mastery": s.avg_mastery,
                    "concept_count": s.concept_count,
                    "mastered_count": s.mastered_count,
                    "struggling_count": s.struggling_count,
                }
                for s in subjects
            ],
            "overall_stats": {
                "total_concepts": len(levels),
                "total_mastered": mastered,
                "in_progress": sum(1 for v in levels if 0 < v < learned),
                "avg_mastery": round(sum(levels) / len(levels), 4) if levels else 0.0,
                "streak_days": streak_days(active, today),
                "total_study_minutes": totals["total_study_minutes"],
                "total_sessions": totals["total_sessions"],
                "questions_answered": answered,
                "accuracy": round(totals["correct_answers"] / answered, 4) if answered else 0.0,
            },
            "mastery_distribution": distribution,
            "recent_activity": series,
            "misconceptions": await self.repo.misconception_counts(user_id),
            "timezone": tz,
            "generated_at": self.now().isoformat(),
        }

    # ---- heatmap ------------------------------------------------------------------------

    async def heatmap(
        self,
        user_id: uuid.UUID,
        *,
        subject_id: uuid.UUID | None = None,
        weeks: int = 8,
        limit: int = 100,
    ) -> dict[str, Any]:
        return await self._cached(
            user_id,
            f"heatmap:{subject_id}:{weeks}:{limit}",
            lambda: self._build_heatmap(user_id, subject_id, weeks, limit),
        )

    async def _build_heatmap(
        self, user_id: uuid.UUID, subject_id: uuid.UUID | None, weeks: int, limit: int
    ) -> dict[str, Any]:
        _, today = await self._today(user_id)
        columns = week_columns(today, weeks)
        rows, total = await self.repo.heatmap(user_id, subject_id=subject_id, limit=limit)
        concepts = []
        for r in rows:
            assessed = r.last_assessed_at.date() if r.last_assessed_at else None
            cells = [
                mastery_at(r.history, day, fallback=r.mastery_level, assessed=assessed)
                for day in columns[:-1]
            ]
            # The latest column is "now": the stored value includes materialised decay.
            cells.append(round(r.mastery_level, 4) if assessed else None)
            concepts.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "subject_id": r.subject_id,
                    "subject_name": r.subject_name,
                    "mastery_level": round(r.mastery_level, 4),
                    "label": mastery_label(r.mastery_level),
                    "attempt_count": r.attempt_count,
                    "last_assessed_at": r.last_assessed_at,
                    "next_review_at": r.next_review_at,
                    "cells": cells,
                }
            )
        return {
            "columns": [d.isoformat() for d in columns],
            "concepts": concepts,
            "total_concepts": total,
            "truncated": total > len(concepts),
        }

    # ---- AI usage -----------------------------------------------------------------------

    async def ai_usage(self, user_id: uuid.UUID, period: Period) -> dict[str, Any]:
        now = self.now()
        start_day = now.date() - timedelta(days=PERIOD_DAYS[period])  # UTC days, like the budget
        since = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)
        rows = await self.repo.ai_usage(user_id, since)
        by_purpose: dict[str, dict[str, Any]] = {}
        by_model: dict[str, dict[str, Any]] = {}
        for purpose, model, count, tokens, cost, failures in rows:
            for bucket, key in ((by_purpose, purpose), (by_model, model)):
                entry = bucket.setdefault(key, {"cost": 0.0, "count": 0, "tokens": 0, "failed": 0})
                entry["cost"] += cost
                entry["count"] += count
                entry["tokens"] += tokens
                entry["failed"] += failures
        for bucket in (by_purpose, by_model):
            for entry in bucket.values():
                entry["cost"] = round(entry["cost"], 6)
        today_rows = await self.repo.ai_daily_cost(
            user_id, datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
        )
        daily = await self.repo.ai_daily_cost(user_id, since)
        return {
            "period": period,
            "since": start_day.isoformat(),
            "total_cost_usd": round(sum(r[4] for r in rows), 6),
            "today_cost_usd": round(sum(r[1] for r in today_rows), 6),
            "daily_budget_usd": self.settings.ai_daily_budget_usd,
            "total_tokens": sum(r[3] for r in rows),
            "total_interactions": sum(r[2] for r in rows),
            "failed_interactions": sum(r[5] for r in rows),
            "by_purpose": dict(sorted(by_purpose.items(), key=lambda kv: -kv[1]["count"])),
            "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["count"])),
            "daily": [
                {"date": d.isoformat(), "cost": round(c, 6), "count": n} for d, c, n in daily
            ],
        }
