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

    # ---- outcome measurement (GSC/GA4) ----
    # Equal baseline and follow-up windows; 28 days avoids weekday imbalance.
    measurement_window_days: int = Field(default=28, ge=7, le=90)
    # Google clamps both integrations to today-2, so a follow-up window is not evaluable until its
    # last day is at least this old. Evaluating sooner reports a partial window as a real result.
    measurement_source_lag_days: int = Field(default=2, ge=0, le=7)
    # Internal/test client rows that must never be measured or counted in coverage metrics.
    measurement_excluded_client_ids: str = ""
    # Clients explicitly cleared to be measured on a GSC/GA4 handle shared with another client.
    # Default is to refuse: a shared property cannot be attributed without verification.
    measurement_shared_handle_overrides: str = ""

    def _uuid_set(self, raw: str) -> frozenset[str]:
        return frozenset(part.strip().casefold() for part in raw.split(",") if part.strip())

    @property
    def excluded_client_ids(self) -> frozenset[str]:
        return self._uuid_set(self.measurement_excluded_client_ids)

    @property
    def shared_handle_overrides(self) -> frozenset[str]:
        return self._uuid_set(self.measurement_shared_handle_overrides)

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
