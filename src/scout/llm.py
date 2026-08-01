# llm.py — LLM client wrappers and prompt-loading utilities for the Scout pipeline.
# Purpose: Provides call_synthesis/call_extraction via OpenRouter with retries, JSON coercion, LangSmith tracing, and per-call logging.
# Scope: Hosts the OpenRouter client singleton, run-id context, prompt file validation, and prompt-log batching into Supabase.
# Consumers: Every investigation + recommendation + report node; run.py calls set_run_id() at bootstrap and flush_prompt_log() at the end.
import hashlib
import json
import logging
import random
import threading
import time
from datetime import UTC, datetime
from importlib.resources import files

import openai
from langsmith import traceable as _traceable
from langsmith.wrappers import wrap_openai

from scout.config import get_config

log = logging.getLogger(__name__)

_openrouter_client = None
_llm_semaphore = None
_sem_lock = threading.Lock()
_run_id: str = ""
_log_buffer: list[dict] = []

# Substrings that mark an OpenRouter/OpenAI APIError as a credit/limit failure (retrying can't fix it).
_KEY_EXHAUSTED_PHRASES = (
    "key limit exceeded",
    "insufficient credits",
    "insufficient_quota",
    "exceeded your current quota",
    "negative balance",
    "payment required",
)


class LLMKeyExhaustedError(BaseException):
    """Fatal, un-swallowable signal that the OpenRouter key is over its credit/spend limit (401/402/403).
    Subclasses BaseException (like KeyboardInterrupt) so NO node's `except Exception` fallback can swallow it — it propagates to run.py for a loud abort instead of an all-fallback run."""


def _is_key_exhausted(e: Exception) -> bool:
    """Return True when an APIError is a credit/auth failure retrying can't fix (key limit, no credits, 401/402/403).
    Excludes 429 rate-limits, which stay a normal transient handled by is_transient/the node retry loops."""
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return True
    if getattr(e, "status_code", None) in (401, 402, 403):
        return True
    msg = str(e).lower()
    return any(p in msg for p in _KEY_EXHAUSTED_PHRASES)


def set_run_id(run_id: str) -> None:
    """Store the current run's UUID so every subsequent prompt-log entry carries it.
    Called once from run.py after generating the run_id; also resets the per-run log buffer."""
    global _run_id, _log_buffer
    _run_id = run_id
    _log_buffer = []


def get_token_total() -> tuple[int, int]:
    """Return (prompt_tokens_sum, completion_tokens_sum) across every log entry buffered this run.
    Read by run.py to report total token usage once the graph completes."""
    p_total = sum(int(e.get("prompt_tokens", 0) or 0) for e in _log_buffer)
    c_total = sum(int(e.get("completion_tokens", 0) or 0) for e in _log_buffer)
    return p_total, c_total


def flush_prompt_log() -> int:
    """Drain the in-memory log buffer into Supabase prompt_log via a single batched insert; returns rows written.
    No-op when the buffer is empty; failures are logged and swallowed so they never block run completion."""
    global _log_buffer
    if not _log_buffer:
        return 0
    try:
        from scout.db.supabase_client import get_sed_client
        sb = get_sed_client()
        rows = []
        for entry in _log_buffer:
            rows.append({
                "run_id": entry.get("run_id") or _run_id or None,
                "node": entry.get("node_name") or "",
                "model": entry.get("model") or "",
                "prompt_tokens": int(entry.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(entry.get("completion_tokens", 0) or 0),
                "total_tokens": int(entry.get("total_tokens", 0) or 0),
                "latency_ms": int(entry.get("latency_ms", 0) or 0) or None,
                "error": entry.get("error"),
                "reasoning_tokens": int(entry.get("reasoning_tokens", 0) or 0) or None,
                "reasoning": entry.get("reasoning"),
            })
        sb.table("prompt_log").insert(rows).execute()
        written = len(rows)
        _log_buffer = []
        return written
    except Exception as e:
        log.warning("[llm] flush_prompt_log failed (buffer kept): %s", e)
        return 0


JSON_INSTRUCTION = "\n\nRespond with ONLY a valid JSON object. No markdown fences. No preamble. No explanation outside the JSON. No extra fields beyond the schema."


def get_openrouter_client():
    """Return the process-wide OpenRouter client singleton, constructing it on first call using config credentials.
    Subsequent calls are O(1); the client wraps the OpenAI SDK pointed at openrouter.ai/api/v1."""
    global _openrouter_client
    if _openrouter_client is None:
        config = get_config()
        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=config.openrouter_api_key,
            timeout=config.llm_timeout_seconds,
            max_retries=config.llm_max_retries,
        )
        # wrap_openai makes each .chat.completions.create a native LangSmith `llm` run
        # (messages, reasoning, token usage, cost) nested under the call_synthesis/extraction chain.
        _openrouter_client = wrap_openai(client) if config.langsmith_tracing else client
    return _openrouter_client


def _get_llm_semaphore():
    """Return the process-wide BoundedSemaphore capping concurrent OpenRouter calls (llm_max_concurrency).
    Lazily sized from config so the LangGraph fan-out can't burst past the provider's rate limit and trip 429s."""
    global _llm_semaphore
    if _llm_semaphore is None:
        with _sem_lock:
            if _llm_semaphore is None:
                _llm_semaphore = threading.BoundedSemaphore(get_config().llm_max_concurrency)
    return _llm_semaphore


def _strip_fences(text: str) -> str:
    """Strip leading ```json/``` fences and a trailing ``` from a model reply before JSON parsing.
    Behaviour-preserving extraction of the logic call_synthesis/call_extraction previously inlined."""
    clean = text.strip()
    for prefix in ("```json", "```"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    if clean.endswith("```"):
        clean = clean[:-3]
    return clean.strip()


def _complete_and_parse(actual_model, provider_pref, system_prompt, user_msg,
                        expect_json, log_entry, node_name, max_tokens=None):
    """Run one OpenRouter completion with a single retry; on output truncation the retry raises max_tokens, on JSON/API errors it re-tries.
    Returns parsed JSON when expect_json else raw text, writing the prompt_log entry on every terminal path. Shared by call_synthesis + call_extraction."""
    config = get_config()
    base_max = max_tokens or config.gemini_max_tokens
    ceiling = getattr(config, "gemini_max_tokens_ceiling", base_max * 2)
    for attempt in range(2):
        max_toks = base_max if attempt == 0 else min(base_max * 2, ceiling)
        try:
            start = time.time()
            extra_body = {}
            if provider_pref:
                extra_body["provider"] = provider_pref
            if config.capture_reasoning:
                extra_body["reasoning"] = {"enabled": True}   # DeepSeek returns it by default; enables Kimi thinking
                extra_body["usage"] = {"include": True}       # OpenRouter returns cost + reasoning_tokens in usage
            with _get_llm_semaphore():
                response = get_openrouter_client().chat.completions.create(
                    model=actual_model,
                    max_tokens=max_toks,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    extra_body=extra_body,
                )
            choice = response.choices[0]
            msg = choice.message
            text = msg.content or ""
            finish = getattr(choice, "finish_reason", None)
            usage = response.usage
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            reasoning = getattr(msg, "reasoning", None)
            if reasoning is None and getattr(msg, "model_extra", None):
                reasoning = msg.model_extra.get("reasoning") or msg.model_extra.get("reasoning_content")
            details = getattr(usage, "completion_tokens_details", None)
            reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0
            log_entry.update(
                {
                    "latency_ms": int((time.time() - start) * 1000),
                    "response_length": len(text),
                    "attempt": attempt + 1,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "reasoning": reasoning,
                    "reasoning_tokens": reasoning_tokens,
                }
            )

            if expect_json:
                if finish == "length":
                    # Output hit max_tokens → JSON is truncated. Retry once with a larger budget
                    # (not transient, so no sleep); fail only if even the raised budget truncates.
                    log_entry["parse_success"] = False
                    log_entry["error"] = f"truncated at max_tokens={max_toks}"
                    if attempt == 0:
                        continue
                    _write_log(log_entry)
                    raise ValueError(
                        f"{node_name or 'llm'} output truncated even at max_tokens={max_toks}; "
                        f"split this node's payload"
                    )
                parsed = json.loads(_strip_fences(text))
                log_entry["parse_success"] = True
                _write_log(log_entry)
                return parsed

            log_entry["parse_success"] = True
            _write_log(log_entry)
            return text

        except json.JSONDecodeError:
            log_entry["parse_success"] = False
            if attempt == 0:
                time.sleep(config.gemini_retry_delay_seconds)
                continue
            _write_log(log_entry)
            raise
        except openai.APIError as e:
            log_entry["error"] = str(e)
            if _is_key_exhausted(e):
                _write_log(log_entry)
                raise LLMKeyExhaustedError(f"OpenRouter rejected {node_name or 'llm'}: {e}") from e
            if attempt == 0:
                # Exponential backoff + jitter for transient API errors (esp. 429); the SDK already
                # retries with Retry-After upstream, so this is the residual fallback layer.
                base = config.gemini_retry_delay_seconds
                time.sleep(base * (2 ** attempt) + random.uniform(0, base))
                continue
            _write_log(log_entry)
            raise


@_traceable(run_type="chain", name="call_synthesis")
def call_synthesis(
    system_prompt: str,
    user_message: str,
    expect_json: bool = True,
    node_name: str = "",
    trigger_key: str = "",
    model: str | None = None,
    pin_provider: bool = True,
    max_tokens: int | None = None,
) -> str | dict:
    """Invoke the synthesis model via OpenRouter with one retry on JSON/API errors and append a prompt_log entry.
    Returns parsed JSON when expect_json else raw text; pin_provider=False skips the provider pin, max_tokens overrides the initial budget (None = config.gemini_max_tokens)."""
    config = get_config()
    actual_model = model or config.gemini_model
    provider_pref = (
        {"order": [config.gemini_provider], "allow_fallbacks": config.llm_allow_provider_fallback}
        if (pin_provider and getattr(config, "gemini_provider", "")) else None
    )
    user_msg = user_message + (JSON_INSTRUCTION if expect_json else "")
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": _run_id,
        "node_name": node_name,
        "trigger_key": trigger_key,
        "model": actual_model,
        "system_prompt_hash": hashlib.md5(system_prompt.encode()).hexdigest(),
        "user_message_length": len(user_msg),
    }

    return _complete_and_parse(
        actual_model, provider_pref, system_prompt, user_msg, expect_json, log_entry, node_name, max_tokens
    )


@_traceable(run_type="chain", name="call_extraction")
def call_extraction(
    system_prompt: str,
    user_message: str,
    expect_json: bool = True,
    node_name: str = "",
    trigger_key: str = "",
) -> str | dict:
    """Invoke the extraction model via OpenRouter with one retry on JSON/API errors and append a prompt_log entry.
    Returns parsed JSON when expect_json else raw text; used by nodes that need fast structured extraction."""
    config = get_config()
    model = config.openrouter_model
    provider_pref = (
        {"order": [config.extraction_provider], "allow_fallbacks": config.llm_allow_provider_fallback}
        if getattr(config, "extraction_provider", "") else None
    )
    user_msg = user_message + (JSON_INSTRUCTION if expect_json else "")
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": _run_id,
        "node_name": node_name,
        "trigger_key": trigger_key,
        "model": model,
        "system_prompt_hash": hashlib.md5(system_prompt.encode()).hexdigest(),
        "user_message_length": len(user_msg),
    }

    return _complete_and_parse(
        model, provider_pref, system_prompt, user_msg, expect_json, log_entry, node_name
    )


def call_extraction_consistent(
    system_prompt: str,
    user_message: str,
    expect_json: bool = True,
    node_name: str = "",
    trigger_key: str = "",
    n: int | None = None,
) -> str | dict:
    """Run call_extraction n times and return the modal result for deterministic self-consistency (R0-4).
    n<=1 (config.extraction_self_consistency_n default) is a transparent pass-through; ties resolve to the first sample. Pure-Python aggregation, no extra LLM judge."""
    samples = n if n is not None else getattr(get_config(), "extraction_self_consistency_n", 1)
    if not samples or samples <= 1:
        return call_extraction(system_prompt, user_message, expect_json, node_name, trigger_key)
    results = []
    for _ in range(int(samples)):
        try:
            results.append(call_extraction(system_prompt, user_message, expect_json, node_name, trigger_key))
        except Exception:
            continue
    if not results:
        return call_extraction(system_prompt, user_message, expect_json, node_name, trigger_key)
    counts: dict[str, int] = {}
    first_by_key: dict[str, object] = {}
    for r in results:
        try:
            k = json.dumps(r, sort_keys=True, default=str) if isinstance(r, (dict, list)) else str(r)
        except Exception:
            k = str(r)
        counts[k] = counts.get(k, 0) + 1
        first_by_key.setdefault(k, r)
    best = max(counts, key=lambda key: counts[key])   # dict preserves insertion order → ties resolve to the first sample
    return first_by_key[best]


REQUIRED_PROMPTS = [
    "scout-ai-response-analysis",
    "scout-blog-classification",
    "scout-recommendation-generation",
    "scout-recommendation-generation-deep",
    "scout-website-diff-analysis",
]


def load_prompt(skill_name: str) -> str:
    """Read a prompt markdown file from scout/prompts/ and return its utf-8 text.
    Raises FileNotFoundError when the skill file is missing; no caching so edits are picked up immediately."""
    return files("scout.prompts").joinpath(f"{skill_name}.md").read_text(encoding="utf-8")


def validate_prompts() -> None:
    """Assert that all REQUIRED_PROMPTS exist on disk before the graph runs; raises FileNotFoundError listing any missing.
    Called once at bootstrap from run.py so a missing prompt fails fast instead of mid-pipeline."""
    prompts_dir = files("scout.prompts")
    missing = [p for p in REQUIRED_PROMPTS if not prompts_dir.joinpath(f"{p}.md").is_file()]
    if missing:
        raise FileNotFoundError(
            f"Missing required prompt files in {prompts_dir}: {', '.join(missing)}"
        )


def _write_log(entry: dict):
    """Append a single prompt-log record to the in-memory buffer for later batching into Supabase.
    The buffer is drained once at run end by flush_prompt_log()."""
    _log_buffer.append(entry)
