from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from ai_visibility.config.settings import Settings


@contextmanager
def connect(settings: Settings, *, autocommit: bool = False) -> Iterator[Connection[Any]]:
    database_url = settings.require_database_url()
    with psycopg.connect(
        database_url,
        autocommit=autocommit,
        row_factory=dict_row,
        connect_timeout=15,
        application_name="ai-visibility",
        options=(f"-c statement_timeout={settings.database_statement_timeout_seconds * 1000}"),
    ) as connection:
        yield connection
