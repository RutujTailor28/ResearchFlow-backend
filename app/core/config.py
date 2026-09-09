"""Application settings via pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ — works regardless of process CWD
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    """Runtime configuration. Database/Supabase vars are optional for Phase 1."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = Field(default="Multi-Agent Research API", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=True, alias="DEBUG")
    api_v1_prefix: str = Field(default="/api/v1", alias="API_V1_PREFIX")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")

    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="CORS_ORIGINS",
    )

    # Optional — OpenRouter for Phase 2 planner
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_model: str | None = Field(default=None, alias="OPENROUTER_MODEL")
    openrouter_timeout_ms: int = Field(default=120_000, alias="OPENROUTER_TIMEOUT_MS")
    openrouter_fallback_models: str = Field(
        default=(
            "meta-llama/llama-3.2-3b-instruct:free,"
            "google/gemma-2-9b-it:free,"
            "mistralai/mistral-7b-instruct:free"
        ),
        alias="OPENROUTER_FALLBACK_MODELS",
    )
    openrouter_http_referer: str | None = Field(
        default=None, alias="OPENROUTER_HTTP_REFERER"
    )
    openrouter_app_title: str = Field(default="ResearchFlow", alias="OPENROUTER_APP_TITLE")

    # Phase 4 research tools
    search_provider: str = Field(default="duckduckgo", alias="SEARCH_PROVIDER")
    research_tool_timeout_s: float = Field(default=30.0, alias="RESEARCH_TOOL_TIMEOUT_S")
    # Number of retries after the first attempt (Phase 10: max 2).
    research_tool_max_retries: int = Field(default=2, alias="RESEARCH_TOOL_MAX_RETRIES")

    # Phase 10 reliability
    run_timeout_s: float = Field(default=900.0, alias="RUN_TIMEOUT_S")
    run_cost_limit_usd: float = Field(default=1.0, alias="RUN_COST_LIMIT_USD")
    openrouter_max_retries: int = Field(default=2, alias="OPENROUTER_MAX_RETRIES")
    research_max_workers: int = Field(default=4, alias="RESEARCH_MAX_WORKERS")

    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_anon_key: str | None = Field(default=None, alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str | None = Field(
        default=None, alias="SUPABASE_SERVICE_ROLE_KEY"
    )

    @field_validator(
        "openrouter_api_key",
        "openrouter_model",
        "openrouter_http_referer",
        "database_url",
        "supabase_url",
        "supabase_anon_key",
        "supabase_service_role_key",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    def get_cors_origins(self) -> List[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if self.frontend_url and self.frontend_url not in origins:
            origins.append(self.frontend_url)
        return origins

    def get_openrouter_model(self) -> str:
        return self.openrouter_model or "openrouter/free"

    def get_openrouter_fallback_models(self) -> List[str]:
        return [
            m.strip()
            for m in self.openrouter_fallback_models.split(",")
            if m.strip()
        ]

    @property
    def supabase_configured(self) -> bool:
        return bool(
            self.supabase_url
            and (self.supabase_service_role_key or self.supabase_anon_key)
        )

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
