from __future__ import annotations

import hashlib
import json
import os
import tomllib
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportProfile(StrEnum):
    decision = "decision"
    detailed = "detailed"


class ProfileSettings(StrictConfigModel):
    max_decision_cards: int = Field(ge=1, le=100)
    max_recommendations: int = Field(ge=1, le=100)
    max_sources_per_card: int = Field(ge=1, le=100)
    max_provider_rows: int = Field(ge=1, le=50)
    max_query_rows: int = Field(ge=1, le=500)
    history_weeks: int = Field(ge=1, le=104)
    include_query_details: bool
    include_full_sov_tables: bool
    include_evidence_appendix: bool
    include_data_quality_appendix: bool


class ReportDefaults(StrictConfigModel):
    default_profile: ReportProfile = ReportProfile.decision
    write_latest_copies: bool = True
    allow_partial: bool = False
    narrative_mode: Literal["reuse_validated"] = "reuse_validated"


class ProfileCollection(StrictConfigModel):
    decision: ProfileSettings
    detailed: ProfileSettings


class ReportingConfig(StrictConfigModel):
    config_version: Literal["1.0"] = "1.0"
    report: ReportDefaults
    profiles: ProfileCollection

    @model_validator(mode="after")
    def detailed_must_not_be_narrower(self) -> ReportingConfig:
        decision = self.profiles.decision
        detailed = self.profiles.detailed
        comparable = (
            "max_decision_cards",
            "max_recommendations",
            "max_sources_per_card",
            "max_provider_rows",
            "max_query_rows",
            "history_weeks",
        )
        if any(getattr(detailed, name) < getattr(decision, name) for name in comparable):
            raise ValueError("detailed profile limits must not be lower than decision limits")
        return self


class ResolvedReportConfig(StrictConfigModel):
    config: ReportingConfig
    profile: ReportProfile
    profile_settings: ProfileSettings
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: str


def _canonical_hash(config: ReportingConfig) -> str:
    encoded = json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_toml_bytes(content: bytes) -> ReportingConfig:
    parsed: Any = tomllib.loads(content.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("report configuration must contain a TOML object")
    return ReportingConfig.model_validate(parsed)


def load_report_config(
    *,
    config_path: Path | None = None,
    profile: ReportProfile | str | None = None,
) -> ResolvedReportConfig:
    """Load strict report configuration with CLI > env > TOML precedence."""
    explicit_env_path = os.getenv("AIVC_REPORT_CONFIG_PATH")
    selected_path = config_path or (Path(explicit_env_path) if explicit_env_path else None)
    if selected_path is not None:
        if not selected_path.is_file():
            raise ValueError(f"Report configuration file does not exist: {selected_path}")
        config = _load_toml_bytes(selected_path.read_bytes())
        source = str(selected_path.resolve())
    else:
        default_path = Path("config/reporting.toml")
        if default_path.is_file():
            config = _load_toml_bytes(default_path.read_bytes())
            source = str(default_path.resolve())
        else:
            packaged = files("aivc.resources.config").joinpath("reporting.toml")
            config = _load_toml_bytes(packaged.read_bytes())
            source = "packaged_default"

    env_profile = os.getenv("AIVC_REPORT_PROFILE")
    selected_profile = ReportProfile(profile or env_profile or config.report.default_profile)
    settings = getattr(config.profiles, selected_profile.value)
    return ResolvedReportConfig(
        config=config,
        profile=selected_profile,
        profile_settings=settings,
        config_hash=_canonical_hash(config),
        source=source,
    )
