from __future__ import annotations

import hashlib
import json
import os
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class ReportingConfig(StrictConfigModel):
    config_version: Literal["1.3"] = "1.3"
    limits: ProfileSettings


class ResolvedReportConfig(StrictConfigModel):
    config: ReportingConfig
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
) -> ResolvedReportConfig:
    """Load the single detailed, client-facing report configuration."""
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

    return ResolvedReportConfig(
        config=config,
        profile_settings=config.limits,
        config_hash=_canonical_hash(config),
        source=source,
    )
