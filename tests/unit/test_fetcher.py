from __future__ import annotations

import httpcore
import httpx
import pytest

from ai_visibility.config.settings import Settings
from ai_visibility.scraping.fetcher import PageFetchError, _clear_robots_cache, fetch_page
from ai_visibility.scraping.transport import PinnedNetworkBackend


@pytest.fixture(autouse=True)
def clear_robots_cache() -> None:
    _clear_robots_cache()


def test_fetcher_revalidates_redirect_and_drops_conditional_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    requests: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.headers.get("if-none-match")))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><title>Done</title><main>Aprio evidence</main></html>",
        )

    monkeypatch.setattr(
        fetcher,
        "validate_public_url",
        lambda url: ["93.184.216.34"],
    )
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )
    result = fetch_page(
        Settings(),
        "https://example.com/start",
        etag='"old"',
    )
    assert result.final_url == "https://example.com/final"
    assert result.title == "Done"
    assert requests.count(("/robots.txt", None)) == 1
    assert ("/start", '"old"') in requests
    assert ("/final", None) in requests


def test_fetcher_enforces_maximum_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"x" * 2_000,
        )

    monkeypatch.setattr(
        fetcher,
        "validate_public_url",
        lambda url: ["93.184.216.34"],
    )
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )
    with pytest.raises(PageFetchError, match="PAGE_FETCH_MAX_BYTES"):
        fetch_page(
            Settings(page_fetch_max_bytes=1_024),
            "https://example.com/large",
        )


def test_pinned_backend_rejects_unvalidated_hostname() -> None:
    backend = PinnedNetworkBackend("example.com", "93.184.216.34")
    with pytest.raises(httpcore.ConnectError, match="unvalidated"):
        backend.connect_tcp("attacker.test", 443)


def test_fetcher_falls_back_to_another_validated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    attempted: list[str] = []

    def transport(_hostname: str, address: str) -> httpx.MockTransport:
        attempted.append(address)

        def handler(request: httpx.Request) -> httpx.Response:
            if address == "93.184.216.1":
                raise httpx.ConnectError("first address unavailable", request=request)
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, text="<html><main>usable evidence</main></html>")

        return httpx.MockTransport(handler)

    monkeypatch.setattr(
        fetcher,
        "validate_public_url",
        lambda url: ["93.184.216.1", "93.184.216.2"],
    )
    monkeypatch.setattr(fetcher, "pinned_transport", transport)
    result = fetch_page(
        Settings(page_fetch_min_text_characters=1),
        "https://example.com/page",
    )
    assert result.main_text == "usable evidence"
    assert attempted == ["93.184.216.1", "93.184.216.2"]


def test_fetcher_retries_temporary_status_and_honors_zero_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    page_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page_attempts
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page_attempts += 1
        if page_attempts == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, text="<html><main>retry worked</main></html>")

    monkeypatch.setattr(fetcher, "validate_public_url", lambda url: ["93.184.216.34"])
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )
    result = fetch_page(
        Settings(page_fetch_min_text_characters=1),
        "https://example.com/retry",
    )
    assert result.main_text == "retry worked"
    assert page_attempts == 2


def test_fetcher_sniffs_mislabeled_html_and_resolves_relative_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text='<html><main>Aprio evidence</main><a href="/services">Services</a></html>',
        )

    monkeypatch.setattr(fetcher, "validate_public_url", lambda url: ["93.184.216.34"])
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )
    result = fetch_page(
        Settings(page_fetch_min_text_characters=1),
        "https://example.com/article",
    )
    assert result.content_type == "text/html"
    assert result.fetch_metadata["content_type_sniffed"] is True
    assert "https://example.com/services" in result.links


def test_fetcher_uses_browser_for_low_quality_static_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"etag": '"static"'},
            text="<html><main>Enable JavaScript</main></html>",
        )

    monkeypatch.setattr(fetcher, "validate_public_url", lambda url: ["93.184.216.34"])
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )
    monkeypatch.setattr(
        fetcher,
        "browser_fallback",
        lambda *args, **kwargs: {
            "final_url": "https://example.com/article",
            "status_code": 200,
            "html": (
                "<html><title>Rendered</title><main>"
                + "Useful rendered Aprio evidence. " * 20
                + "</main></html>"
            ),
        },
    )
    result = fetch_page(
        Settings(
            page_fetch_browser_fallback_enabled=True,
            page_fetch_min_text_characters=100,
        ),
        "https://example.com/article",
    )
    assert result.fetch_method == "browser_html"
    assert result.etag is None
    assert result.extraction_quality["usable"] is True
    assert result.fetch_metadata["browser_fallback_attempted"] is True


def test_browser_fallback_failure_preserves_static_result_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html><main>Enable JavaScript</main></html>")

    monkeypatch.setattr(fetcher, "validate_public_url", lambda url: ["93.184.216.34"])
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )

    def unavailable(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("optional browser is unavailable")

    monkeypatch.setattr(fetcher, "browser_fallback", unavailable)
    result = fetch_page(
        Settings(page_fetch_browser_fallback_enabled=True),
        "https://example.com/article",
    )
    assert result.fetch_method == "http_html"
    assert result.extraction_quality["usable"] is False
    assert result.fetch_metadata["browser_fallback_error_type"] == "RuntimeError"


def test_fetcher_rejects_declared_oversize_before_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "5000"},
            content=b"small",
        )

    monkeypatch.setattr(fetcher, "validate_public_url", lambda url: ["93.184.216.34"])
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )
    with pytest.raises(PageFetchError, match="PAGE_FETCH_MAX_BYTES"):
        fetch_page(Settings(page_fetch_max_bytes=1_024), "https://example.com/large")


def test_fetcher_revalidates_redirect_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    checked: list[str] = []

    def validate(url: str) -> list[str]:
        checked.append(url)
        if "127.0.0.1" in url:
            raise ValueError("non-public redirect")
        return ["93.184.216.34"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    monkeypatch.setattr(fetcher, "validate_public_url", validate)
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="non-public redirect"):
        fetch_page(Settings(), "https://example.com/start")
    assert checked == ["https://example.com/start", "http://127.0.0.1/private"]


def test_robots_policy_is_cached_for_same_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    import ai_visibility.scraping.fetcher as fetcher

    robots_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal robots_requests
        if request.url.path == "/robots.txt":
            robots_requests += 1
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, text="<html><main>Useful page text</main></html>")

    monkeypatch.setattr(fetcher, "validate_public_url", lambda url: ["93.184.216.34"])
    monkeypatch.setattr(
        fetcher,
        "pinned_transport",
        lambda hostname, address: httpx.MockTransport(handler),
    )
    settings = Settings(page_fetch_min_text_characters=1)
    fetch_page(settings, "https://example.com/one")
    fetch_page(settings, "https://example.com/two")
    assert robots_requests == 1
