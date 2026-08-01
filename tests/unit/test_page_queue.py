from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from ai_visibility.config.settings import Settings
from ai_visibility.jobs.queue import (
    _snapshot_extraction_usable,
    _snapshot_structured_data,
    process_page_jobs,
)
from ai_visibility.scraping.fetcher import FetchResult


def _result(url: str) -> FetchResult:
    quality = {"quality": "high", "usable": True}
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        title="Example",
        main_text="Useful evidence",
        raw_content_hash="raw",
        main_text_hash="text",
        fetch_method="http_html",
        extraction_quality=quality,
        fetch_metadata={"extraction_quality": quality},
    )


def test_snapshot_metadata_preserves_extraction_quality() -> None:
    payload = _snapshot_structured_data(_result("https://example.com"))
    assert _snapshot_extraction_usable(payload) is True
    assert payload["_fetch_metadata"]["extraction_quality"]["quality"] == "high"


def test_page_jobs_use_bounded_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    import ai_visibility.jobs.queue as queue

    jobs: list[dict[str, Any]] = [
        {
            "id": index,
            "client_id": "client",
            "normalized_url": f"https://site{index}.example/page",
            "attempts": 1,
        }
        for index in range(4)
    ]
    active = 0
    maximum_active = 0
    active_lock = threading.Lock()
    persisted: list[int] = []

    monkeypatch.setattr(queue, "_claim_job", lambda settings: jobs.pop(0) if jobs else None)

    def fetch(settings: Settings, url: str, **kwargs: object) -> FetchResult:
        nonlocal active, maximum_active
        with active_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with active_lock:
            active -= 1
        return _result(url)

    monkeypatch.setattr(queue, "fetch_page", fetch)
    monkeypatch.setattr(
        queue,
        "_persist_fetch",
        lambda settings, job, result: persisted.append(int(job["id"])),
    )
    monkeypatch.setattr(queue, "_fail_job", lambda settings, job, error: None)
    outcome = process_page_jobs(
        Settings(page_fetch_max_workers=2, page_fetch_per_domain_delay_seconds=0),
        limit=4,
    )
    assert outcome == {"completed": 4, "failed_or_retried": 0}
    assert sorted(persisted) == [0, 1, 2, 3]
    assert maximum_active == 2


def test_concurrent_workers_preserve_per_domain_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    import ai_visibility.jobs.queue as queue

    jobs: list[dict[str, Any]] = [
        {
            "id": index,
            "client_id": "client",
            "normalized_url": f"https://same.example/page-{index}",
            "attempts": 1,
        }
        for index in range(2)
    ]
    starts: list[float] = []
    monkeypatch.setattr(queue, "_claim_job", lambda settings: jobs.pop(0) if jobs else None)

    def fetch(settings: Settings, url: str, **kwargs: object) -> FetchResult:
        starts.append(time.monotonic())
        return _result(url)

    monkeypatch.setattr(queue, "fetch_page", fetch)
    monkeypatch.setattr(queue, "_persist_fetch", lambda settings, job, result: None)
    outcome = process_page_jobs(
        Settings(page_fetch_max_workers=2, page_fetch_per_domain_delay_seconds=0.08),
        limit=2,
    )
    assert outcome == {"completed": 2, "failed_or_retried": 0}
    assert len(starts) == 2
    assert abs(starts[1] - starts[0]) >= 0.06
