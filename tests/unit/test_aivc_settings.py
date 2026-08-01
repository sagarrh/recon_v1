import pytest
from pydantic import SecretStr

from aivc.config.settings import AivcSettings


def test_same_supabase_project_is_verified() -> None:
    settings = AivcSettings(
        database_url=SecretStr("postgresql://postgres:pw@db.abc123.supabase.co:5432/postgres"),
        supabase_url="https://abc123.supabase.co",
    )
    assert settings.validate_same_project()["same_project_verified"] is True


def test_different_supabase_projects_fail_closed() -> None:
    settings = AivcSettings(
        database_url=SecretStr("postgresql://postgres:pw@db.abc123.supabase.co:5432/postgres"),
        supabase_url="https://different.supabase.co",
    )
    with pytest.raises(RuntimeError, match="different Supabase projects"):
        settings.validate_same_project()
