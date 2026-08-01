-- prompt_log_reasoning.sql — persist the model's reasoning trace alongside the per-call token log.
-- Idempotent + additive (safe to re-run). Apply in the Supabase SQL editor before the next live run.
-- llm._complete_and_parse now reads choice.message.reasoning + usage.completion_tokens_details.reasoning_tokens
-- (dropped before this change) and flush_prompt_log writes them here; reasoning also lands natively in LangSmith via wrap_openai.
ALTER TABLE prompt_log ADD COLUMN IF NOT EXISTS reasoning_tokens int;
ALTER TABLE prompt_log ADD COLUMN IF NOT EXISTS reasoning text;
