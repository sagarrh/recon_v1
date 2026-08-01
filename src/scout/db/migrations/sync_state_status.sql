-- sync_state_status.sql — R1-2: optional status/error columns on the sync_state heartbeat table.
-- Idempotent + additive (safe to re-run). Apply in the Supabase SQL editor, then deploy the scout-sync edge fn.
ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS status text DEFAULT 'ok';
ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS error_message text;
ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS updated_at timestamptz;
