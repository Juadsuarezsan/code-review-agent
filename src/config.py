"""Application settings loaded from environment variables (see ``.env.example``).

Configuration is kept separate from the domain: every module that needs a value
receives it through :func:`get_settings` or as an explicit argument, never by
reading ``os.environ`` directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Dated model ID pinned for the whole application. Undated aliases drift silently.
PINNED_MODEL = "claude-sonnet-4-5-20250929"


class Settings(BaseSettings):
    """Runtime configuration.

    Every field maps to one environment variable. Defaults are safe for local
    development without any API key: the pipeline then runs in deterministic
    linter-only mode.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # LLM
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default=PINNED_MODEL, alias="ANTHROPIC_MODEL")
    llm_timeout_seconds: float = Field(default=30.0, alias="LLM_TIMEOUT_SECONDS", gt=0)
    llm_max_retries: int = Field(default=3, alias="LLM_MAX_RETRIES", ge=1, le=10)
    llm_max_tokens: int = Field(default=1200, alias="LLM_MAX_TOKENS", ge=64, le=16000)

    # GitHub
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    github_api_url: str = Field(default="https://api.github.com", alias="GITHUB_API_URL")
    github_timeout_seconds: float = Field(default=20.0, alias="GITHUB_TIMEOUT_SECONDS", gt=0)

    # Review behaviour
    max_comments_per_pr: int = Field(default=10, alias="MAX_COMMENTS_PER_PR", ge=1, le=100)
    max_comments_per_file: int = Field(default=5, alias="MAX_COMMENTS_PER_FILE", ge=1, le=50)
    max_diff_bytes: int = Field(default=400_000, alias="MAX_DIFF_BYTES", ge=1_000)
    semgrep_bin: str | None = Field(default=None, alias="SEMGREP_BIN")

    # API hardening
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000", alias="CORS_ORIGINS"
    )
    rate_limit: str = Field(default="20/minute", alias="RATE_LIMIT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=False, alias="LOG_JSON")

    # Storage
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    review_cache_size: int = Field(default=256, alias="REVIEW_CACHE_SIZE", ge=0)

    # Observability (LangSmith). LangGraph picks these up from the environment on its own;
    # they are declared here so ``.env.example`` documents them and ``/health`` can report them.
    langchain_tracing_v2: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    langchain_api_key: str | None = Field(default=None, alias="LANGCHAIN_API_KEY")
    langchain_project: str = Field(default="code-review-agent", alias="LANGCHAIN_PROJECT")

    @field_validator(
        "anthropic_api_key", "github_token", "database_url", "semgrep_bin", mode="before"
    )
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        """Treat empty strings from ``.env`` files as unset."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        """Return the configured CORS origins as a list, dropping blanks and wildcards."""
        origins = [o.strip() for o in self.cors_origins.split(",")]
        return [o for o in origins if o and o != "*"]

    @property
    def llm_enabled(self) -> bool:
        """True when an Anthropic API key is configured."""
        return bool(self.anthropic_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton."""
    return Settings()
