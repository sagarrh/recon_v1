from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import structlog
from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt

from ai_visibility.config.settings import Settings
from ai_visibility.scraping.browser import browser_fallback
from ai_visibility.scraping.html import extract_html
from ai_visibility.scraping.pdf import extract_pdf
from ai_visibility.scraping.security import validate_public_url
from ai_visibility.scraping.transport import pinned_transport
from ai_visibility.utils.hashing import sha256_bytes, sha256_text

logger = structlog.get_logger(__name__)

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_BLOCK_PAGE_MARKERS = (
    "access denied",
    "enable javascript",
    "verify you are human",
    "checking your browser",
    "captcha",
    "cloudflare ray id",
)
_ROBOTS_CACHE: dict[tuple[str, str], tuple[float, bool | RobotFileParser]] = {}
_ROBOTS_CACHE_LOCK = threading.Lock()


class PageFetchError(RuntimeError):
    pass


class PageNotModified(PageFetchError):
    pass


class RetryablePageFetchError(PageFetchError):
    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    title: str | None
    main_text: str
    raw_content_hash: str
    main_text_hash: str
    fetch_method: str
    etag: str | None = None
    last_modified_header: str | None = None
    structured_data: Any = field(default_factory=dict)
    headings: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    extraction_quality: dict[str, Any] = field(default_factory=dict)
    fetch_metadata: dict[str, Any] = field(default_factory=dict)


def _clear_robots_cache() -> None:
    with _ROBOTS_CACHE_LOCK:
        _ROBOTS_CACHE.clear()


def _robots_policy_allows(
    policy: bool | RobotFileParser,
    user_agent: str,
    url: str,
) -> bool:
    return policy if isinstance(policy, bool) else policy.can_fetch(user_agent, url)


def _robots_allowed(client: httpx.Client, settings: Settings, url: str) -> bool:
    parts = urlsplit(url)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    cache_key = (origin.casefold(), settings.page_fetch_user_agent)
    now = time.monotonic()
    with _ROBOTS_CACHE_LOCK:
        cached = _ROBOTS_CACHE.get(cache_key)
    if cached is not None and cached[0] > now:
        return _robots_policy_allows(cached[1], settings.page_fetch_user_agent, url)

    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    with client.stream("GET", robots_url) as response:
        if response.status_code in {401, 403}:
            policy: bool | RobotFileParser = False
        elif response.status_code >= 400:
            policy = True
        elif response.status_code in _REDIRECT_STATUSES:
            policy = False
        else:
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > min(settings.page_fetch_max_bytes, 512_000):
                    policy = False
                    break
                chunks.append(chunk)
            else:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(b"".join(chunks).decode("utf-8", errors="replace").splitlines())
                policy = parser
    if settings.page_fetch_robots_cache_seconds > 0:
        with _ROBOTS_CACHE_LOCK:
            _ROBOTS_CACHE[cache_key] = (
                now + settings.page_fetch_robots_cache_seconds,
                policy,
            )
    return _robots_policy_allows(policy, settings.page_fetch_user_agent, url)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return min(60.0, max(0.0, float(value)))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return min(60.0, max(0.0, (parsed - datetime.now(UTC)).total_seconds()))


def _retry_wait(retry_state: RetryCallState) -> float:
    outcome = retry_state.outcome
    exception = outcome.exception() if outcome is not None else None
    if isinstance(exception, RetryablePageFetchError) and exception.retry_after_seconds is not None:
        return exception.retry_after_seconds
    attempt_number = int(retry_state.attempt_number)
    return float(min(4.0, 0.5 * (2 ** max(0, attempt_number - 1))))


def _declared_content_type(response: httpx.Response) -> str:
    return str(response.headers.get("content-type", "")).split(";", 1)[0].strip().casefold()


def _detected_content_type(declared: str, content: bytes) -> tuple[str, bool]:
    if content.startswith(b"%PDF") or declared == "application/pdf":
        return "application/pdf", declared != "application/pdf"
    prefix = content[:1_024].lstrip().lower()
    looks_like_html = prefix.startswith((b"<!doctype html", b"<html")) or any(
        marker in prefix for marker in (b"<head", b"<body", b"<title", b"<main")
    )
    if declared in _HTML_TYPES or looks_like_html:
        return "text/html", declared not in _HTML_TYPES
    raise PageFetchError(f"Unsupported page content type: {declared or 'unknown'}")


def _extraction_quality(
    main_text: str,
    title: str | None,
    minimum_characters: int,
) -> dict[str, Any]:
    stripped = main_text.strip()
    sample = f"{title or ''}\n{stripped[:2_000]}".casefold()
    block_marker = next((marker for marker in _BLOCK_PAGE_MARKERS if marker in sample), None)
    character_count = len(stripped)
    word_count = len(stripped.split())
    usable = character_count >= minimum_characters and block_marker is None
    quality = (
        "blocked"
        if block_marker
        else "empty"
        if not stripped
        else "high"
        if character_count >= max(1_000, minimum_characters * 3)
        else "medium"
        if usable
        else "low"
    )
    return {
        "quality": quality,
        "usable": usable,
        "character_count": character_count,
        "word_count": word_count,
        "block_page_marker": block_marker,
    }


def _bounded_html_text(
    extracted: dict[str, Any],
    max_characters: int,
) -> tuple[str, bool]:
    main_text = str(extracted.get("main_text") or "")
    if len(main_text) <= max_characters:
        return main_text, False
    return main_text[:max_characters], True


@retry(
    retry=retry_if_exception_type(
        (httpx.TimeoutException, httpx.NetworkError, RetryablePageFetchError)
    ),
    stop=stop_after_attempt(3),
    wait=_retry_wait,
    reraise=True,
)
def fetch_page(
    settings: Settings,
    url: str,
    *,
    etag: str | None = None,
    last_modified_header: str | None = None,
) -> FetchResult:
    started = time.monotonic()
    current = url
    headers = {
        "User-Agent": settings.page_fetch_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.1",
    }
    conditional_headers: dict[str, str] = {}
    if etag:
        conditional_headers["If-None-Match"] = etag
    if last_modified_header:
        conditional_headers["If-Modified-Since"] = last_modified_header
    timeout = httpx.Timeout(settings.page_fetch_timeout_seconds)

    for redirect_number in range(settings.page_fetch_max_redirects + 1):
        addresses = validate_public_url(current)
        hostname = urlsplit(current).hostname
        if hostname is None:
            raise PageFetchError("Page URL has no hostname.")
        redirect_target: str | None = None
        last_network_error: httpx.HTTPError | None = None
        for address in addresses:
            transport = pinned_transport(hostname, address)
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=False,
                    headers=headers,
                    transport=transport,
                    trust_env=False,
                ) as client:
                    if not _robots_allowed(client, settings, current):
                        raise PageFetchError("Page retrieval is disallowed by robots.txt.")
                    request_headers = conditional_headers if redirect_number == 0 else {}
                    with client.stream("GET", current, headers=request_headers) as response:
                        if response.status_code == 304:
                            raise PageNotModified("Page content was not modified.")
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise PageFetchError("Redirect response had no Location header.")
                            if redirect_number >= settings.page_fetch_max_redirects:
                                raise PageFetchError("Page exceeded the configured redirect limit.")
                            redirect_target = urljoin(current, location)
                            break
                        if response.status_code in _RETRYABLE_STATUSES:
                            raise RetryablePageFetchError(
                                f"Page temporarily returned HTTP {response.status_code}.",
                                retry_after_seconds=_retry_after_seconds(
                                    response.headers.get("retry-after")
                                ),
                            )
                        if response.status_code >= 400:
                            raise PageFetchError(f"Page returned HTTP {response.status_code}.")
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                declared_size = int(content_length)
                            except ValueError:
                                declared_size = 0
                            if declared_size > settings.page_fetch_max_bytes:
                                raise PageFetchError("Page exceeded PAGE_FETCH_MAX_BYTES.")
                        chunks: list[bytes] = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > settings.page_fetch_max_bytes:
                                raise PageFetchError("Page exceeded PAGE_FETCH_MAX_BYTES.")
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        declared_type = _declared_content_type(response)
                        content_type, content_sniffed = _detected_content_type(
                            declared_type,
                            content,
                        )
                        browser_attempted = False
                        browser_error_type: str | None = None
                        text_truncated = False
                        final_url = str(response.url)
                        status_code = response.status_code
                        if content_type == "application/pdf":
                            try:
                                extracted = extract_pdf(
                                    content,
                                    max_pages=settings.page_fetch_max_pdf_pages,
                                    max_extracted_characters=(
                                        settings.page_fetch_max_extracted_characters
                                    ),
                                )
                            except (OSError, ValueError) as exc:
                                raise PageFetchError(f"PDF extraction failed: {exc}") from exc
                            method = "http_pdf"
                            main_text = str(extracted["main_text"])
                        else:
                            extracted = extract_html(
                                content,
                                response.encoding,
                                base_url=final_url,
                            )
                            main_text, text_truncated = _bounded_html_text(
                                extracted,
                                settings.page_fetch_max_extracted_characters,
                            )
                            quality = _extraction_quality(
                                main_text,
                                extracted.get("title"),
                                settings.page_fetch_min_text_characters,
                            )
                            method = "http_html"
                            if (
                                not quality["usable"]
                                and settings.page_fetch_browser_fallback_enabled
                            ):
                                browser_attempted = True
                                try:
                                    rendered = browser_fallback(
                                        current,
                                        timeout_seconds=settings.page_fetch_timeout_seconds,
                                        max_bytes=settings.page_fetch_max_bytes,
                                    )
                                    rendered_html = str(rendered["html"]).encode("utf-8")
                                    rendered_status = rendered.get("status_code")
                                    if rendered_status is not None and int(rendered_status) >= 400:
                                        raise PageFetchError(
                                            "Browser-rendered page returned HTTP "
                                            f"{rendered_status}."
                                        )
                                    rendered_extracted = extract_html(
                                        rendered_html,
                                        "utf-8",
                                        base_url=str(rendered["final_url"]),
                                    )
                                    rendered_text, rendered_truncated = _bounded_html_text(
                                        rendered_extracted,
                                        settings.page_fetch_max_extracted_characters,
                                    )
                                    rendered_quality = _extraction_quality(
                                        rendered_text,
                                        rendered_extracted.get("title"),
                                        settings.page_fetch_min_text_characters,
                                    )
                                    if (
                                        rendered_quality["character_count"]
                                        > quality["character_count"]
                                    ):
                                        content = rendered_html
                                        extracted = rendered_extracted
                                        main_text = rendered_text
                                        quality = rendered_quality
                                        text_truncated = rendered_truncated
                                        final_url = str(rendered["final_url"])
                                        if rendered_status is not None:
                                            status_code = int(rendered_status)
                                        method = "browser_html"
                                except Exception as exc:
                                    browser_error_type = type(exc).__name__
                                    logger.warning(
                                        "browser_fallback_failed",
                                        page_url=current,
                                        error_type=browser_error_type,
                                    )
                        quality = _extraction_quality(
                            main_text,
                            extracted.get("title"),
                            settings.page_fetch_min_text_characters,
                        )
                        quality["text_truncated"] = text_truncated
                        elapsed_ms = round((time.monotonic() - started) * 1_000)
                        metadata = {
                            "byte_count": len(content),
                            "elapsed_ms": elapsed_ms,
                            "declared_content_type": declared_type,
                            "content_type_sniffed": content_sniffed,
                            "redirect_count": redirect_number,
                            "browser_fallback_attempted": browser_attempted,
                            "browser_fallback_error_type": browser_error_type,
                            "extraction_quality": quality,
                        }
                        logger.info(
                            "page_fetch_completed",
                            page_url=url,
                            final_url=final_url,
                            status_code=status_code,
                            fetch_method=method,
                            byte_count=len(content),
                            elapsed_ms=elapsed_ms,
                            extraction_quality=quality["quality"],
                        )
                        return FetchResult(
                            requested_url=url,
                            final_url=final_url,
                            status_code=status_code,
                            content_type=content_type,
                            title=extracted.get("title"),
                            main_text=main_text,
                            raw_content_hash=sha256_bytes(content),
                            main_text_hash=sha256_text(main_text),
                            fetch_method=method,
                            etag=(
                                response.headers.get("etag") if method != "browser_html" else None
                            ),
                            last_modified_header=(
                                response.headers.get("last-modified")
                                if method != "browser_html"
                                else None
                            ),
                            structured_data=extracted.get("structured_data", {}),
                            headings=list(extracted.get("headings", [])),
                            links=list(extracted.get("links", [])),
                            extraction_quality=quality,
                            fetch_metadata=metadata,
                        )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_network_error = exc
                logger.debug(
                    "page_fetch_address_failed",
                    page_url=current,
                    error_type=type(exc).__name__,
                )
                continue
        if redirect_target is not None:
            current = redirect_target
            continue
        if last_network_error is not None:
            raise last_network_error
        raise PageFetchError("Page retrieval did not produce a response.")
    raise PageFetchError("Page retrieval did not produce a response.")
