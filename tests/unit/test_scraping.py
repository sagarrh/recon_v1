from __future__ import annotations

import socket
import sys
from io import BytesIO
from types import ModuleType
from typing import Any

import pytest
from pypdf import PdfWriter

from ai_visibility.scraping.browser import browser_fallback
from ai_visibility.scraping.fetcher import FetchResult
from ai_visibility.scraping.html import extract_html
from ai_visibility.scraping.pdf import extract_pdf
from ai_visibility.scraping.security import validate_public_url
from ai_visibility.scraping.snapshots import analyze_page_company, diff_snapshots


def test_private_ip_is_blocked() -> None:
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url("http://127.0.0.1/private")


def test_excessive_url_length_is_blocked() -> None:
    with pytest.raises(ValueError, match="safety length"):
        validate_public_url("https://example.com/" + "x" * 8_200)


def test_internal_dns_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 80))],
    )
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url("https://example.com/data")


def test_mixed_public_and_private_dns_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443)),
        ],
    )
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url("https://example.com/data")


def test_html_extraction_removes_executable_content() -> None:
    result = extract_html(
        b"<html><head><title>Example</title><script>bad()</script></head>"
        b"<body><main><h1>Aprio</h1><p>Useful guidance.</p></main></body></html>"
    )
    assert result["title"] == "Example"
    assert "Useful guidance" in result["main_text"]
    assert "bad()" not in result["main_text"]


def test_html_extraction_normalizes_links_and_records_method() -> None:
    result = extract_html(
        b'<html><body><main>Useful</main><a href="/about">About</a></body></html>',
        base_url="https://example.com/articles/one",
    )
    assert result["links"] == ["https://example.com/about"]
    assert result["structured_data"]["extraction_method"] in {
        "trafilatura",
        "beautifulsoup_fallback",
    }


def test_pdf_and_snapshot_analysis() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    pdf = extract_pdf(buffer.getvalue())
    assert pdf["structured_data"]["page_count"] == 1

    fetch = FetchResult(
        requested_url="https://www.aprio.com/page",
        final_url="https://www.aprio.com/page",
        status_code=200,
        content_type="text/html",
        title="Aprio guidance",
        main_text="Aprio is a recommended provider.",
        raw_content_hash="raw",
        main_text_hash="text",
        fetch_method="http_html",
        headings=["Choose Aprio"],
        links=["https://aprio.com/services"],
    )
    evidence = analyze_page_company(fetch, "Aprio", ["aprio.com"])
    assert evidence["mention_count"] == 1
    assert evidence["mentioned_in_title"] is True
    assert evidence["publisher_is_company"] is True
    assert evidence["links_to_official_domain"] is True
    assert diff_snapshots("old text", "completely different")["material_change"] is True


def test_pdf_extraction_enforces_page_limit() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError, match="page limit"):
        extract_pdf(buffer.getvalue(), max_pages=1)


def test_browser_fallback_fails_closed_without_optional_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.browser as browser

    monkeypatch.setattr(
        browser,
        "validate_public_url",
        lambda url: ["93.184.216.34"],
    )
    with pytest.raises(RuntimeError, match="Playwright fallback is unavailable"):
        browser_fallback("https://example.com/")


def test_browser_fallback_tries_validated_addresses_and_blocks_unsafe_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.browser as browser_module

    launches: list[str] = []
    route_decisions: list[str] = []

    class FakeRequest:
        def __init__(self, url: str, resource_type: str) -> None:
            self.url = url
            self.resource_type = resource_type

    class FakeRoute:
        def __init__(self, request: FakeRequest) -> None:
            self.request = request

        def abort(self) -> None:
            route_decisions.append("abort")

        def continue_(self) -> None:
            route_decisions.append("continue")

    class FakePage:
        url = "https://example.com/final"

        def __init__(self, should_fail: bool) -> None:
            self.should_fail = should_fail

        def route(self, pattern: str, callback: Any) -> None:
            callback(FakeRoute(FakeRequest("https://attacker.test/x", "script")))
            callback(FakeRoute(FakeRequest("https://example.com/image", "image")))
            callback(FakeRoute(FakeRequest("https://example.com:8443/admin", "script")))
            callback(FakeRoute(FakeRequest("https://example.com/app.js", "script")))

        def goto(self, *args: Any, **kwargs: Any) -> Any:
            if self.should_fail:
                raise RuntimeError("first address unavailable")
            return type("Response", (), {"status": 200})()

        def content(self) -> str:
            return "<html><main>Rendered evidence</main></html>"

        def title(self) -> str:
            return "Rendered"

    class FakeContext:
        def __init__(self, should_fail: bool) -> None:
            self.should_fail = should_fail

        def new_page(self) -> FakePage:
            return FakePage(self.should_fail)

    class FakeBrowser:
        def __init__(self, should_fail: bool) -> None:
            self.should_fail = should_fail

        def new_context(self, **kwargs: Any) -> FakeContext:
            assert kwargs["accept_downloads"] is False
            assert kwargs["service_workers"] == "block"
            return FakeContext(self.should_fail)

        def close(self) -> None:
            return None

    class FakeChromium:
        def launch(self, *, headless: bool, args: list[str]) -> FakeBrowser:
            rule = args[0]
            launches.append(rule)
            return FakeBrowser(should_fail="93.184.216.1" in rule)

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, *args: Any) -> None:
            return None

    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakeManager()  # type: ignore[attr-defined]
    package = ModuleType("playwright")
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(
        browser_module,
        "validate_public_url",
        lambda url: ["93.184.216.1", "93.184.216.2"],
    )

    result = browser_fallback("https://example.com/")
    assert result["status_code"] == 200
    assert len(launches) == 2
    assert any("93.184.216.1" in rule for rule in launches)
    assert any("93.184.216.2" in rule for rule in launches)
    assert route_decisions.count("abort") == 6
    assert route_decisions.count("continue") == 2
