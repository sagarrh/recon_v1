from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings.

    DATABASE_URL is optional during import so offline tests and command help work.
    Database-backed commands call ``require_database_url`` before doing any work.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: SecretStr | None = None
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    pipeline_version: str = "0.1.0"
    report_output_dir: Path = Path("./output")
    page_fetch_user_agent: str = "AIVisibilityIntelligence/0.1"
    page_fetch_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    page_fetch_max_bytes: int = Field(default=10_000_000, ge=1_024)
    page_fetch_max_redirects: int = Field(default=5, ge=0, le=10)
    page_fetch_per_domain_delay_seconds: float = Field(default=0.5, ge=0, le=10)
    page_fetch_robots_cache_seconds: int = Field(default=1_800, ge=0, le=86_400)
    page_fetch_min_text_characters: int = Field(default=200, ge=0, le=100_000)
    page_fetch_max_extracted_characters: int = Field(
        default=2_000_000,
        ge=1_000,
        le=20_000_000,
    )
    page_fetch_max_pdf_pages: int = Field(default=200, ge=1, le=2_000)
    page_fetch_browser_fallback_enabled: bool = False
    page_fetch_max_workers: int = Field(default=4, ge=1, le=16)
    page_job_lease_seconds: int = Field(default=900, ge=30, le=86_400)
    database_statement_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    openai_api_key: SecretStr | None = None

    def require_database_url(self) -> str:
        if self.database_url is None or not self.database_url.get_secret_value().strip():
            raise RuntimeError(
                "DATABASE_URL is required. Copy .env.example to .env and set the direct "
                "Supabase PostgreSQL connection string."
            )
        return self.database_url.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
