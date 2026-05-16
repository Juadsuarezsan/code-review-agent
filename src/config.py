from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-5", alias="ANTHROPIC_MODEL")
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    max_comments_per_pr: int = Field(default=10, alias="MAX_COMMENTS_PER_PR")

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
