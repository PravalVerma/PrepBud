"""Application configuration (pydantic-settings).

All values come from environment variables (or a `.env` file). Secrets are never
hard-coded. Model selection for every AI task is configuration-driven (ADR-003).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENAI_BASE_URL = "https://api.openai.com/v1"


class LLMTaskConfig(BaseModel):
    """Per-task LLM configuration (AI_SYSTEM_DESIGN §2.2)."""

    provider: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 2000
    streaming: bool = False
    response_format: Literal["json", "text"] = "text"
    dimensions: int | None = None


def _default_llm_tasks() -> dict[str, LLMTaskConfig]:
    return {
        "tutor_explanation": LLMTaskConfig(
            provider="openai", model="gpt-4o", temperature=0.7, max_tokens=2000, streaming=True
        ),
        "question_generation": LLMTaskConfig(
            provider="openai",
            model="gpt-4o-mini",
            temperature=0.8,
            max_tokens=1000,
            response_format="json",
        ),
        "answer_evaluation": LLMTaskConfig(
            provider="openai",
            model="gpt-4o-mini",
            temperature=0.2,
            max_tokens=800,
            response_format="json",
        ),
        "misconception_detection": LLMTaskConfig(
            provider="openai",
            model="gpt-4o",
            temperature=0.3,
            max_tokens=500,
            response_format="json",
        ),
        "concept_extraction": LLMTaskConfig(
            provider="openai",
            model="gpt-4o",
            temperature=0.3,
            max_tokens=2000,
            response_format="json",
        ),
        "session_summary": LLMTaskConfig(
            provider="openai", model="gpt-4o-mini", temperature=0.5, max_tokens=500
        ),
        "embedding": LLMTaskConfig(
            provider="openai", model="text-embedding-3-small", dimensions=1536
        ),
    }


class LLMProviderConfig(BaseModel):
    """An OpenAI-compatible endpoint (OpenAI, Ollama, vLLM, LM Studio, Together, Groq, …).

    AC-3.7: switching provider is a configuration change — point a task's ``provider``
    at another entry here (or change ``LLM_BASE_URL`` for the default provider).
    """

    base_url: str = OPENAI_BASE_URL
    api_key: SecretStr = SecretStr("")
    timeout_seconds: float = 120.0
    max_retries: int = 2
    # `response_format={"type": "json_object"}`; when False the prompt alone asks for JSON.
    supports_json_mode: bool = True
    # Send the embedding task's `dimensions` (OpenAI text-embedding-3 models accept it).
    supports_dimensions_param: bool = True


class ModelPricing(BaseModel):
    """USD per million tokens (used for `ai_interactions.cost_estimate`)."""

    input_per_1m: float = 0.0
    output_per_1m: float = 0.0


def _default_pricing() -> dict[str, ModelPricing]:
    return {
        "gpt-4o": ModelPricing(input_per_1m=2.50, output_per_1m=10.00),
        "gpt-4o-mini": ModelPricing(input_per_1m=0.15, output_per_1m=0.60),
        "text-embedding-3-small": ModelPricing(input_per_1m=0.02),
        "text-embedding-3-large": ModelPricing(input_per_1m=0.13),
    }


class ContentProcessingSettings(BaseModel):
    """Document pipeline parameters (AI_SYSTEM_DESIGN §5)."""

    upload_max_bytes: int = 50 * 1024 * 1024
    max_pages: int = 500
    chunk_min_tokens: int = 500
    chunk_max_tokens: int = 1000
    chunk_overlap_tokens: int = 100
    # Concept extraction sends several chunks per LLM call, several calls at once.
    extraction_batch_tokens: int = 3000
    extraction_concurrency: int = 6
    extraction_cache_ttl_seconds: int = 86_400
    max_concepts_per_document: int = 200
    relationship_batch_size: int = 80
    existing_concepts_context: int = 80
    embedding_batch_size: int = 64
    # Pages yielding fewer characters than this are OCR'd (scanned / image-only pages).
    ocr_min_chars_per_page: int = 25
    ocr_language: str = "eng"
    ocr_resolution_dpi: int = 200
    processing_lock_ttl_seconds: int = 900
    task_soft_time_limit_seconds: int = 600
    task_max_retries: int = 3


class LearningEngineSettings(BaseModel):
    """Learning Engine parameters (LEARNING_ENGINE §10)."""

    mastery_learned_threshold: float = 0.80
    mastery_prerequisite_threshold: float = 0.60
    mastery_review_trigger: float = 0.70
    default_max_interactions: int = 20
    default_time_budget_minutes: int = 30
    max_concepts_per_session: int = 5
    frustration_consecutive_failures: int = 5
    target_success_rate: float = 0.70
    difficulty_adjustment_step: float = 0.1
    bkt_slip_rate: float = 0.10
    bkt_guess_rate: float = 0.25
    bkt_learning_rate: float = 0.10
    sm2_initial_ease_factor: float = 2.5
    sm2_minimum_ease_factor: float = 1.3
    decay_enabled: bool = True
    decay_check_interval_hours: int = 24
    max_content_chunks: int = 5
    context_token_budget: int = 4000
    default_concepts_per_day: int = 3
    # --- engine internals (not in LEARNING_ENGINE §10; sensible defaults) -----------------
    # Move on from a concept after this many questions even if not yet mastered.
    max_attempts_per_concept: int = 6
    # Frustration response (LEARNING_ENGINE §7.3).
    frustration_difficulty_drop: float = 0.2
    frustration_response_time_factor: float = 2.5
    # A second frustration activation in one session ends it with an encouraging message.
    frustration_max_activations: int = 2
    # Redis: session state (DATA_MODEL §5: 2 hours) and mastery cache (30 minutes).
    session_state_ttl_seconds: int = 7200
    mastery_cache_ttl_seconds: int = 1800
    mastery_history_limit: int = 50
    # Stored questions within this difficulty distance of the target are reused (assumption #9).
    question_reuse_window: float = 0.15
    # Study plans (LEARNING_ENGINE §8): reviews are planned this many days ahead.
    study_plan_horizon_days: int = 14
    # Daily maintenance (decay + plan refresh) runs at this hour (UTC) via Celery Beat.
    daily_maintenance_hour_utc: int = 3
    # Materialised decay adds a mastery-history point per this much forgetting.
    decay_history_step: float = 0.05


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # --- Application ---------------------------------------------------------
    app_name: str = "School in a Box"
    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # --- PostgreSQL ----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://siab:siab@localhost:5432/siab"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_echo: bool = False

    # --- Redis ---------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- HTTP ----------------------------------------------------------------
    frontend_url: str = "http://localhost:3000"

    # --- Supabase Auth -------------------------------------------------------
    supabase_url: str = ""
    supabase_jwt_secret: SecretStr = SecretStr("")
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    jwks_cache_ttl_seconds: int = 600
    jwt_leeway_seconds: int = 10

    # --- Rate limiting (SECURITY_MODEL §5.1) ----------------------------------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 100
    rate_limit_auth_per_minute: int = 10

    # --- Health ----------------------------------------------------------------
    health_check_timeout_seconds: float = 2.0

    rate_limit_uploads_per_hour: int = 5
    # SECURITY_MODEL 5.1: session starts per hour, WebSocket messages per minute.
    rate_limit_sessions_per_hour: int = 10
    rate_limit_ws_messages_per_minute: int = 60
    # Single-use WebSocket tickets (the browser never holds the JWT).
    ws_ticket_ttl_seconds: int = 60

    # --- Vector store (Qdrant) -------------------------------------------------
    qdrant_url: str = ""
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_sections_collection: str = "document_sections"
    qdrant_timeout_seconds: float = 10.0

    # --- Object storage (S3-compatible) ----------------------------------------
    s3_endpoint_url: str = ""
    # Endpoint used inside presigned URLs, when browsers reach S3 by another host
    # than the API does (e.g. Docker networking). Defaults to s3_endpoint_url.
    s3_public_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_presign_expiry_seconds: int = 3600

    # --- Background jobs (Celery) ----------------------------------------------
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_task_always_eager: bool = False
    # Queue the API publishes to and workers consume (separate stacks can share Redis).
    celery_queue: str = "default"

    # --- AI ----------------------------------------------------------------------
    llm_default_provider: str = "openai"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    llm_tasks: dict[str, LLMTaskConfig] = Field(default_factory=_default_llm_tasks)
    ai_pricing: dict[str, ModelPricing] = Field(default_factory=_default_pricing)
    # Store full prompts/responses in ai_interactions.metadata (SECURITY_MODEL §7.3).
    ai_log_content: bool = True
    ai_daily_budget_usd: float = 5.00
    ai_session_token_budget: int = 50_000
    content: ContentProcessingSettings = Field(default_factory=ContentProcessingSettings)
    learning_engine: LearningEngineSettings = Field(default_factory=LearningEngineSettings)

    @field_validator("llm_tasks", mode="before")
    @classmethod
    def _merge_llm_tasks(cls, value: Any) -> Any:
        """Overlay configured tasks onto the defaults, field by field.

        ``LLM_TASKS__CONCEPT_EXTRACTION__MODEL=llama3.1`` changes only that model.
        """
        if not isinstance(value, dict):
            return value
        merged: dict[str, Any] = {k: v.model_dump() for k, v in _default_llm_tasks().items()}
        for name, cfg in value.items():
            override = cfg.model_dump() if isinstance(cfg, BaseModel) else cfg
            key = str(name).lower()
            merged[key] = (
                {**merged.get(key, {}), **override} if isinstance(override, dict) else override
            )
        return merged

    @field_validator("ai_pricing", mode="before")
    @classmethod
    def _merge_pricing(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {**{k: v.model_dump() for k, v in _default_pricing().items()}, **value}

    def llm_provider(self, name: str) -> LLMProviderConfig:
        """Provider config by name. The default provider falls back to LLM_BASE_URL/LLM_API_KEY."""
        if name in self.llm_providers:
            return self.llm_providers[name]
        if name == self.llm_default_provider:
            return LLMProviderConfig(
                base_url=self.llm_base_url or OPENAI_BASE_URL, api_key=self.llm_api_key
            )
        raise KeyError(name)

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend_url(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def embedding_dimensions(self) -> int:
        return self.llm_tasks["embedding"].dimensions or 1536

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.frontend_url.split(",") if o.strip()]

    @property
    def jwks_url(self) -> str:
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        if self.supabase_url:
            return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        return ""

    @property
    def jwt_issuer(self) -> str | None:
        if self.supabase_url:
            return f"{self.supabase_url.rstrip('/')}/auth/v1"
        return None

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
