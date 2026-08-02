from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AivcSettings(BaseSettings):
    """Shared settings used only at producer/orchestration boundaries."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: SecretStr | None = None
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_key: SecretStr | None = None
    pipeline_version: str = "0.1.0"
    report_output_dir: Path = Path("./output")
    aivc_orchestration_enabled: bool = True
    aivc_max_parallel_stages: int = Field(default=2, ge=1, le=8)
    aivc_allow_partial_bundle: bool = False
    aivc_recon_legacy_ai_analysis_enabled: bool = False
    aivc_delivery_mode: str = Field(default="configured", pattern="^(configured|disabled)$")
    aivc_stage_lease_seconds: int = Field(default=900, ge=30, le=86_400)
    aivc_bundle_output_dir: Path = Path("./output")

    def require_database_url(self) -> str:
        if self.database_url is None or not self.database_url.get_secret_value().strip():
            raise RuntimeError("DATABASE_URL is required for database-backed commands.")
        return self.database_url.get_secret_value()

    def require_supabase_key(self) -> str:
        key = self.supabase_service_role_key or self.supabase_key
        if key is None or not key.get_secret_value().strip():
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for Recon persistence.")
        return key.get_secret_value()

    def validate_same_project(self) -> dict[str, str | bool | None]:
        """Fail only when both project references are derivable and disagree."""
        database_ref = _database_project_ref(self.require_database_url())
        supabase_ref = _supabase_project_ref(self.supabase_url)
        if database_ref and supabase_ref and database_ref != supabase_ref:
            raise RuntimeError(
                "DATABASE_URL and SUPABASE_URL resolve to different Supabase projects."
            )
        return {
            "database_project_ref": database_ref,
            "supabase_project_ref": supabase_ref,
            "same_project_verified": bool(database_ref and supabase_ref),
        }


def _supabase_project_ref(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    match = re.fullmatch(r"([a-z0-9]+)\.supabase\.co", host)
    return match.group(1) if match else None


def _database_project_ref(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    direct = re.fullmatch(r"db\.([a-z0-9]+)\.supabase\.co", host)
    if direct:
        return direct.group(1)
    # Supabase pooler usernames commonly use postgres.<project-ref>.
    username = unquote(parsed.username or "").lower()
    pooled = re.fullmatch(r"postgres\.([a-z0-9]+)", username)
    return pooled.group(1) if pooled else None


@lru_cache(maxsize=1)
def get_aivc_settings() -> AivcSettings:
    return AivcSettings()
