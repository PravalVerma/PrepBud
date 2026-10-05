"""Phase 3 configuration (per-task LLM config, providers, pricing) and document rules."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.config import OPENAI_BASE_URL, Settings
from app.domain.content import (
    ALLOWED_UPLOADS,
    DocumentRead,
    UploadUrlRequest,
    can_transition,
    public_metadata,
    sanitize_filename,
    title_from_filename,
)
from app.services.content.retriever import reciprocal_rank_fusion


class TestLLMConfig:
    def test_defaults_follow_ai_system_design(self) -> None:
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.llm_tasks["concept_extraction"].model == "gpt-4o"
        assert s.llm_tasks["concept_extraction"].response_format == "json"
        assert s.llm_tasks["embedding"].dimensions == 1536
        assert s.embedding_dimensions == 1536
        assert s.ai_pricing["gpt-4o-mini"].input_per_1m == 0.15

    def test_env_override_changes_one_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_TASKS__CONCEPT_EXTRACTION__MODEL", "llama3.1:70b")
        monkeypatch.setenv("LLM_TASKS__CONCEPT_EXTRACTION__PROVIDER", "ollama")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        task = s.llm_tasks["concept_extraction"]
        assert (task.provider, task.model) == ("ollama", "llama3.1:70b")
        assert task.temperature == 0.3 and task.response_format == "json"  # defaults kept
        assert s.llm_tasks["tutor_explanation"].model == "gpt-4o"  # other tasks untouched

    def test_json_override_and_new_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_TASKS", '{"custom": {"provider": "x", "model": "y"}}')
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.llm_tasks["custom"].model == "y"
        assert "embedding" in s.llm_tasks

    def test_provider_switch_is_configuration_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-3.7: any OpenAI-compatible endpoint via config."""
        monkeypatch.setenv("LLM_PROVIDERS__OLLAMA__BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_PROVIDERS__OLLAMA__SUPPORTS_JSON_MODE", "false")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        ollama = s.llm_provider("ollama")
        assert ollama.base_url == "http://localhost:11434/v1"
        assert ollama.supports_json_mode is False
        default = s.llm_provider("openai")
        assert default.base_url == OPENAI_BASE_URL
        with pytest.raises(KeyError):
            s.llm_provider("unknown")

    def test_default_provider_uses_llm_base_url(self) -> None:
        s = Settings(_env_file=None, llm_base_url="https://api.groq.com/openai/v1", llm_api_key="k")  # type: ignore[call-arg]
        provider = s.llm_provider("openai")
        assert provider.base_url == "https://api.groq.com/openai/v1"
        assert provider.api_key.get_secret_value() == "k"

    def test_pricing_merges_with_defaults(self) -> None:
        s = Settings(_env_file=None, ai_pricing={"llama3": {"input_per_1m": 0.1}})  # type: ignore[call-arg]
        assert s.ai_pricing["llama3"].input_per_1m == 0.1
        assert "gpt-4o" in s.ai_pricing

    def test_celery_urls_default_to_redis(self) -> None:
        s = Settings(_env_file=None, redis_url="redis://r:1/0")  # type: ignore[call-arg]
        assert s.broker_url == s.result_backend_url == "redis://r:1/0"
        s = Settings(_env_file=None, celery_broker_url="redis://b/1")  # type: ignore[call-arg]
        assert s.broker_url == "redis://b/1"


class TestDocumentRules:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("chapter5.pdf", "chapter5.pdf"),
            ("../../etc/passwd.txt", "passwd.txt"),
            ("C:\\Users\\me\\Notes Final.PDF", "Notes Final.pdf"),
            ("we!rd$name@#.pdf", "we_rd_name.pdf"),
            ("....pdf", "document.pdf"),
            ("noext", "noext"),
            ("x" * 300 + ".pdf", "x" * 150 + ".pdf"),
        ],
    )
    def test_sanitize_filename(self, raw: str, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    def test_title_from_filename(self) -> None:
        assert title_from_filename("Chapter_5 -  Quadratics.pdf") == "Chapter 5 - Quadratics"
        assert title_from_filename(".pdf") == "Untitled document"
        assert title_from_filename("../dir/Ch 5 – Vectors.pdf") == "Ch 5 – Vectors"
        assert title_from_filename("README") == "README"

    def test_status_transitions(self) -> None:
        assert can_transition("pending", "processing")
        assert can_transition(None, "processing")
        assert can_transition("processing", "ready")
        assert can_transition("processing", "failed")
        assert can_transition("failed", "processing")
        assert not can_transition("ready", "processing")
        assert not can_transition("pending", "ready")

    @pytest.mark.parametrize(
        ("mime", "filename"),
        [(m, f"file{exts[0]}") for m, exts in ALLOWED_UPLOADS.items()]
        + [("image/jpeg", "photo.JPEG")],
    )
    def test_whitelisted_uploads_accepted(self, mime: str, filename: str) -> None:
        req = UploadUrlRequest(filename=filename, mime_type=mime.upper(), file_size_bytes=10)
        assert req.mime_type == mime

    @pytest.mark.parametrize(
        "body",
        [
            {"filename": "a.exe", "mime_type": "application/x-msdownload", "file_size_bytes": 1},
            {"filename": "a.pdf", "mime_type": "image/png", "file_size_bytes": 1},
            {"filename": "a", "mime_type": "text/plain", "file_size_bytes": 1},
            {"filename": "a.pdf", "mime_type": "application/pdf", "file_size_bytes": 0},
            {"filename": "a\x00.pdf", "mime_type": "application/pdf", "file_size_bytes": 1},
            {"filename": "", "mime_type": "application/pdf", "file_size_bytes": 1},
            {"filename": "a.pdf", "mime_type": "application/pdf", "file_size_bytes": 1, "x": 1},
        ],
    )
    def test_rejected_uploads(self, body: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            UploadUrlRequest.model_validate(body)

    def test_public_metadata_hides_internals(self) -> None:
        meta = {"page_count": 3, "task_id": "t", "attempts": 2, "error": {"code": "X"}}
        assert public_metadata(meta) == {"page_count": 3, "error": {"code": "X"}}
        assert public_metadata(None) == {}

    def test_document_read_defaults(self) -> None:
        doc = DocumentRead.model_validate(
            {
                "id": uuid.uuid4(),
                "title": "t",
                "source_filename": "t.pdf",
                "mime_type": "application/pdf",
                "file_size_bytes": None,
                "processing_status": None,
                "processing_metadata": None,
                "subject_id": None,
                "course_id": None,
                "uploaded_at": None,
                "processed_at": None,
            }
        )
        assert doc.processing_status == "pending" and doc.processing_metadata == {}


class TestRRF:
    def test_items_found_by_both_rank_first(self) -> None:
        a, b, c, d = (uuid.uuid4() for _ in range(4))
        fused = reciprocal_rank_fusion({"keyword": [a, b, c], "semantic": [d, b]})
        assert fused[0][0] == b
        assert fused[0][2] == ["keyword", "semantic"]
        assert {i for i, _, _ in fused} == {a, b, c, d}
        scores = [s for _, s, _ in fused]
        assert scores == sorted(scores, reverse=True)

    def test_single_ranking_preserves_order(self) -> None:
        ids = [uuid.uuid4() for _ in range(5)]
        assert [i for i, _, _ in reciprocal_rank_fusion({"keyword": ids})] == ids

    def test_empty(self) -> None:
        assert reciprocal_rank_fusion({}) == []
