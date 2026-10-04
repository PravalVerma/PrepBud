"""Settings, envelope helpers, mastery labels, logging."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from app.config import LearningEngineSettings
from app.core.logging import JSONFormatter, request_id_ctx, user_id_ctx
from app.core.middleware import RateLimitResult
from app.domain.common import Pagination, build_meta, envelope, mastery_label, paginated
from tests.support import build_settings


class TestSettings:
    def test_cors_origins_split_and_normalised(self) -> None:
        s = build_settings(frontend_url="http://localhost:3000/, https://app.example.com ,")
        assert s.cors_origins == ["http://localhost:3000", "https://app.example.com"]

    def test_jwks_url_derived_from_supabase_url(self) -> None:
        s = build_settings(supabase_url="https://abc.supabase.co/")
        assert s.jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
        assert s.jwt_issuer == "https://abc.supabase.co/auth/v1"

    def test_explicit_jwks_url_wins(self) -> None:
        s = build_settings(supabase_jwks_url="https://keys.example/jwks.json")
        assert s.jwks_url == "https://keys.example/jwks.json"

    def test_no_supabase_url_means_no_jwks_or_issuer(self) -> None:
        s = build_settings(supabase_url="")
        assert s.jwks_url == ""
        assert s.jwt_issuer is None

    def test_llm_tasks_configured_per_task(self) -> None:
        tasks = build_settings().llm_tasks
        assert {"tutor_explanation", "question_generation", "answer_evaluation"} <= set(tasks)
        assert tasks["embedding"].dimensions == 1536
        assert tasks["tutor_explanation"].streaming is True

    def test_learning_engine_defaults_match_docs(self) -> None:
        le = LearningEngineSettings()
        assert le.mastery_learned_threshold == 0.80
        assert le.frustration_consecutive_failures == 5
        assert le.sm2_minimum_ease_factor == 1.3

    def test_production_flag(self) -> None:
        assert build_settings(app_env="production").is_production
        assert not build_settings().is_production

    def test_secret_not_exposed_in_repr(self) -> None:
        s = build_settings(supabase_jwt_secret="super-secret-value")
        assert "super-secret-value" not in repr(s)


class TestMasteryLabel:
    @pytest.mark.parametrize(
        ("level", "label"),
        [
            (0.0, "novice"),
            (0.19, "novice"),
            (0.2, "beginner"),
            (0.4, "intermediate"),
            (0.59, "intermediate"),
            (0.6, "proficient"),
            (0.8, "mastered"),
            (1.0, "mastered"),
        ],
    )
    def test_labels(self, level: float, label: str) -> None:
        assert mastery_label(level) == label

    @pytest.mark.parametrize("level", [-0.01, 1.01])
    def test_out_of_range(self, level: float) -> None:
        with pytest.raises(ValueError, match="within"):
            mastery_label(level)


class TestEnvelope:
    @pytest.mark.parametrize(
        ("total", "per_page", "pages"), [(0, 20, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2)]
    )
    def test_total_pages(self, total: int, per_page: int, pages: int) -> None:
        assert Pagination.build(total=total, page=1, per_page=per_page).total_pages == pages

    def test_envelope_carries_request_id(self) -> None:
        request = SimpleNamespace(state=SimpleNamespace(request_id="rid-42"))
        env = envelope(request, {"x": 1})  # type: ignore[arg-type]
        dumped = env.model_dump(mode="json")
        assert dumped["data"] == {"x": 1}
        assert dumped["meta"]["request_id"] == "rid-42"
        assert "pagination" not in dumped["meta"]

    def test_paginated_meta(self) -> None:
        request = SimpleNamespace(state=SimpleNamespace())
        env = paginated(request, [1, 2], total=12, page=2, per_page=2)  # type: ignore[arg-type]
        assert env.meta.pagination.total_pages == 6
        assert env.meta.request_id is None

    def test_build_meta_without_request(self) -> None:
        assert build_meta(None).request_id is None


def test_rate_limit_result_headers() -> None:
    r = RateLimitResult(allowed=True, limit=100, remaining=99, reset_at=1_700_000_000)
    assert r.headers() == {
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": "99",
        "X-RateLimit-Reset": "1700000000",
    }


def test_json_log_formatter_includes_context() -> None:
    rid, uid = request_id_ctx.set("req-1"), user_id_ctx.set("user-1")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        record.path = "/x"
        out = json.loads(JSONFormatter().format(record))
    finally:
        request_id_ctx.reset(rid)
        user_id_ctx.reset(uid)
    assert out["message"] == "hello world"
    assert out["request_id"] == "req-1"
    assert out["user_id"] == "user-1"
    assert out["path"] == "/x"
    assert out["level"] == "INFO"


def test_json_log_formatter_renders_exceptions() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    out = json.loads(JSONFormatter().format(record))
    assert "ValueError: boom" in out["exc_info"]
