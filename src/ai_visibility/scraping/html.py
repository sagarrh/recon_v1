from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin, urlsplit

import trafilatura
from bs4 import BeautifulSoup


def extract_html(
    content: bytes,
    encoding: str | None = None,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    text = content.decode(encoding or "utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    for element in soup(["script", "style", "noscript", "iframe", "object"]):
        element.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    extracted = trafilatura.extract(
        text,
        include_comments=False,
        include_tables=True,
        output_format="txt",
    )
    if not extracted:
        extracted = soup.get_text(" ", strip=True)
        extraction_method = "beautifulsoup_fallback"
    else:
        extraction_method = "trafilatura"
    structured_data: list[Any] = []
    original = BeautifulSoup(text, "html.parser")
    for node in original.select('script[type="application/ld+json"]'):
        try:
            structured_data.append(json.loads(node.get_text()))
        except (json.JSONDecodeError, TypeError):
            continue
    links: list[str] = []
    document_base = base_url
    base_node = original.select_one("base[href]")
    if base_node is not None and base_url:
        document_base = urljoin(base_url, str(base_node.get("href")))
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        resolved = urljoin(document_base, href) if document_base else href
        if urlsplit(resolved).scheme.casefold() in {"http", "https"}:
            links.append(resolved)
    return {
        "title": title,
        "main_text": extracted or "",
        "structured_data": {
            "json_ld": structured_data,
            "extraction_method": extraction_method,
        },
        "headings": [
            heading.get_text(" ", strip=True)
            for heading in soup.select("h1, h2, h3")
            if heading.get_text(" ", strip=True)
        ],
        "links": list(dict.fromkeys(links)),
    }
