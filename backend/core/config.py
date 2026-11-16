from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", env_file_encoding="utf-8-sig")

    app_mode: Literal["mock", "live"] = "mock"
    data_dir: Path = ROOT / "data"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    openai_search_model: str = "gpt-5.4-mini"
    search_provider: Literal["openai"] = "openai"
    request_timeout_seconds: float = Field(default=45, ge=1, le=120)
    max_references: int = Field(default=15, ge=1, le=15)
    search_query_limit: int = Field(default=4, ge=1, le=8)
    search_max_requests: int = Field(default=6, ge=2, le=12)
    search_candidate_limit: int = Field(default=30, ge=1, le=60)
    search_timeout_seconds: float = Field(default=75, ge=1, le=120)
    search_run_timeout_seconds: float = Field(default=240, ge=10, le=600)
    search_retry_count: int = Field(default=1, ge=0, le=2)

    @field_validator("openai_model", "openai_search_model", mode="before")
    @classmethod
    def default_model_if_blank(cls, value, info):
        defaults = {"openai_model": "gpt-4.1-mini", "openai_search_model": "gpt-5.4-mini"}
        return value.strip() if isinstance(value, str) and value.strip() else defaults[info.field_name]

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key.get_secret_value().strip() and self.openai_model.strip())

    @property
    def search_configured(self) -> bool:
        return self.openai_configured and bool(self.openai_search_model.strip())
