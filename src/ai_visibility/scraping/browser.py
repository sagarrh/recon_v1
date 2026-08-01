from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ai_visibility.scraping.security import validate_public_url


def browser_fallback(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    max_bytes: int = 10_000_000,
) -> dict[str, Any]:
    """Isolated opt-in JavaScript fallback.

    The default installation intentionally does not install or invoke Playwright.
    Install the ``browser`` extra and browser binaries before explicitly using
    this fallback.
    """

    addresses = validate_public_url(url)
    initial_parts = urlsplit(url)
    hostname = initial_parts.hostname
    if hostname is None:
        raise ValueError("Browser URL has no hostname.")
    initial_scheme = initial_parts.scheme.casefold()
    initial_port = initial_parts.port or (443 if initial_scheme == "https" else 80)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright fallback is unavailable; install the 'browser' extra."
        ) from exc
    last_error: Exception | None = None
    with sync_playwright() as playwright:
        for address in addresses:
            browser = playwright.chromium.launch(
                headless=True,
                args=[(f"--host-resolver-rules=MAP {hostname} {address}, EXCLUDE localhost")],
            )
            try:
                context = browser.new_context(
                    java_script_enabled=True,
                    accept_downloads=False,
                    service_workers="block",
                )
                page = context.new_page()

                def route_request(route: Any) -> None:
                    request_url = route.request.url
                    parts = urlsplit(request_url)
                    request_scheme = parts.scheme.casefold()
                    request_port = parts.port or (443 if request_scheme == "https" else 80)
                    if (
                        request_scheme != initial_scheme
                        or request_port != initial_port
                        or (parts.hostname or "").casefold() != hostname.casefold()
                        or route.request.resource_type in {"font", "image", "media"}
                    ):
                        route.abort()
                        return
                    route.continue_()

                page.route("**/*", route_request)
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(timeout_seconds * 1000),
                )
                final_url = page.url
                validate_public_url(final_url)
                final_parts = urlsplit(final_url)
                final_scheme = final_parts.scheme.casefold()
                final_port = final_parts.port or (443 if final_scheme == "https" else 80)
                if (
                    (final_parts.hostname or "").casefold() != hostname.casefold()
                    or final_scheme != initial_scheme
                    or final_port != initial_port
                ):
                    raise ValueError("Browser navigation left the pinned origin.")
                html = page.content()
                if len(html.encode("utf-8")) > max_bytes:
                    raise ValueError("Browser page exceeded the configured byte limit.")
                return {
                    "final_url": final_url,
                    "status_code": response.status if response else None,
                    "title": page.title(),
                    "html": html,
                }
            except Exception as exc:
                last_error = exc
            finally:
                browser.close()
    if last_error is not None:
        raise last_error
    raise RuntimeError("Browser fallback had no validated address to try.")
