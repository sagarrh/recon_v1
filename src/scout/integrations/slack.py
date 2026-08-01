# slack.py — Slack Incoming Webhook delivery for Scout reports and alerts.
# Purpose: Posts intel digests and real-time alerts to two separate Slack webhooks; honors slack_enabled config flag.
# Scope: Thin requests.post wrapper, helpers for chunking long messages and truncating block_kit blocks to Slack limits.
# Consumers: scout/nodes/slack_delivery.py; no other callers should post to Slack directly.
import logging

import requests

from scout.config import get_config

logger = logging.getLogger(__name__)

_MAX_BLOCK_TEXT = 2900
_MAX_MESSAGE_CHARS = 38000


def post_intel(text: str, blocks: list | None = None) -> bool:
    """Post to the intel webhook (weekly digest style); returns True on success or when Slack is disabled (no-op).
    No-op success when slack_enabled is false OR the intel webhook URL is empty — so callers don't need to guard."""
    config = get_config()
    if not config.slack_enabled or not config.slack_intel_webhook_url:
        return True
    return _post(config.slack_intel_webhook_url, text, blocks)


def post_alert(text: str, blocks: list | None = None) -> bool:
    """Post to the alerts webhook (urgent/high-priority); returns True on success or when Slack is disabled (no-op).
    Same no-op semantics as post_intel but targets the alerts webhook URL configured in ScoutConfig."""
    config = get_config()
    if not config.slack_enabled or not config.slack_alerts_webhook_url:
        return True
    return _post(config.slack_alerts_webhook_url, text, blocks)


def _post(url: str, text: str, blocks: list | None) -> bool:
    """Internal POST helper: sends {text, blocks?} to a webhook URL and treats non-200 or non-'ok' body as failure.
    Catches RequestException and logs a warning so Slack flakiness never crashes the pipeline."""
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200 or resp.text.strip() != "ok":
            logger.warning(f"[slack] webhook returned {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except requests.exceptions.RequestException as e:
        logger.warning(f"[slack] webhook post failed: {e}")
        return False


def chunk_text(text: str, limit: int = _MAX_MESSAGE_CHARS) -> list[str]:
    """Split text into chunks of at most `limit` chars, preferring \\n\\n and then \\n boundaries over hard cuts.
    Returns a single-element list when text fits; used to respect Slack's per-message character ceiling."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    return chunks


def truncate_block(text: str, limit: int = _MAX_BLOCK_TEXT) -> str:
    """Hard-cap a Slack block text section at `limit` chars, appending a '…(truncated)' marker when shortened.
    Respects Slack's ~3000-char per-block limit without attempting to preserve word boundaries."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n…(truncated)"
