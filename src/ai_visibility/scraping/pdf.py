from __future__ import annotations

from io import BytesIO
from typing import Any

from pypdf import PdfReader


def extract_pdf(
    content: bytes,
    *,
    max_pages: int = 200,
    max_extracted_characters: int = 2_000_000,
) -> dict[str, Any]:
    reader = PdfReader(BytesIO(content))
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise ValueError("PDF is encrypted and could not be opened safely.") from exc
        if not unlocked:
            raise ValueError("PDF is encrypted and requires a password.")
    if len(reader.pages) > max_pages:
        raise ValueError(f"PDF exceeds the configured page limit of {max_pages}.")
    extracted_pages: list[str] = []
    extracted_characters = 0
    for page in reader.pages:
        page_text = page.extract_text() or ""
        extracted_characters += len(page_text)
        if extracted_characters > max_extracted_characters:
            raise ValueError("PDF exceeds the configured extracted-text character limit.")
        extracted_pages.append(page_text)
    text = "\n\n".join(extracted_pages)
    metadata: Any = reader.metadata or {}
    return {
        "title": str(metadata.get("/Title")) if metadata.get("/Title") else None,
        "main_text": text,
        "structured_data": {
            "page_count": len(reader.pages),
            "author": metadata.get("/Author"),
        },
        "headings": [],
        "links": [],
    }
