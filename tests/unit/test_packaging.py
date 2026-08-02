from importlib.resources import files
from pathlib import Path

from ai_visibility.database.migrations import migration_directory
from ai_visibility.reports.validation import schema_path
from aivc.reporting.narrative import load_client_report_system_prompt
from scout.llm import REQUIRED_PROMPTS, load_prompt


def test_packaged_migrations_match_root_canonical_files() -> None:
    root = Path(__file__).resolve().parents[2]
    packaged = migration_directory()
    for source in sorted((root / "migrations").glob("*.sql")):
        assert packaged.joinpath(source.name).read_text(encoding="utf-8") == source.read_text(
            encoding="utf-8"
        )


def test_packaged_report_schema_matches_root_canonical_file() -> None:
    root = Path(__file__).resolve().parents[2]
    assert schema_path().read_text(encoding="utf-8") == (
        root / "schemas" / "company_intelligence_report.schema.json"
    ).read_text(encoding="utf-8")


def test_required_scout_prompts_are_packaged() -> None:
    prompt_root = files("scout.prompts")
    for name in REQUIRED_PROMPTS:
        assert prompt_root.joinpath(f"{name}.md").is_file()
        assert load_prompt(name).strip()


def test_client_report_prompt_is_packaged() -> None:
    assert load_client_report_system_prompt().startswith("You are the editorial intelligence layer")


def test_signal_bundle_schemas_are_packaged_and_match_root() -> None:
    root = Path(__file__).resolve().parents[2] / "schemas"
    packaged = files("aivc.resources.schemas")
    for name in ("signal_bundle.schema.json", "combined_signal_bundle.schema.json"):
        assert packaged.joinpath(name).read_text(encoding="utf-8") == (root / name).read_text(
            encoding="utf-8"
        )
