"""Application configuration (pydantic-settings).

All values come from environment variables (or a `.env` file). Secrets are never
hard-coded. Model selection for every AI task is configuration-driven (ADR-003).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- Later phases ----------------------------------------------------------
    qdrant_url: str = ""
    qdrant_api_key: SecretStr = SecretStr("")
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: SecretStr = SecretStr("")
    llm_default_provider: str = "openai"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_tasks: dict[str, LLMTaskConfig] = Field(default_factory=_default_llm_tasks)
    ai_daily_budget_usd: float = 5.00
    ai_session_token_budget: int = 50_000
    learning_engine: LearningEngineSettings = Field(default_factory=LearningEngineSettings)

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
