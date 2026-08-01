-- scout_assets_dedupe_fix.sql — LIVE-E2E fixes (CRITICAL-1 + attribution dedupe), SELF-HEALING.
-- Run the WHOLE file once in the Supabase SQL editor. Every earlier failure was this file's
-- final CREATE INDEX hitting existing NULL-window duplicates and rolling back the whole
-- transaction (including the NOTIFY). This version removes the duplicates itself first.

-- [1] scout_assets: plain 4-column dedupe (PostgREST on_conflict cannot target expression
--     indexes; the original COALESCE index 42P10-failed every upsert). Idempotent.
UPDATE scout_assets SET content_url = '' WHERE content_url IS NULL;
ALTER TABLE scout_assets ALTER COLUMN content_url SET DEFAULT '';
ALTER TABLE scout_assets ALTER COLUMN content_url SET NOT NULL;
DROP INDEX IF EXISTS scout_assets_dedupe_uidx;
CREATE UNIQUE INDEX IF NOT EXISTS scout_assets_dedupe_uidx
    ON scout_assets (client_id, recommendation_id, asset_type, content_url);

-- [2] scout_asset_attribution: normalize windowless rows to the writer's sentinel date, then
--     dedupe IN THIS TRANSACTION, then build a plain unique index (sentinel values make
--     NULLS NOT DISTINCT unnecessary — and this is PG14-safe).
UPDATE scout_asset_attribution SET window_start = '0001-01-01' WHERE window_start IS NULL;
UPDATE scout_asset_attribution SET window_end   = '0001-01-01' WHERE window_end   IS NULL;
DELETE FROM scout_asset_attribution a
 USING scout_asset_attribution b
 WHERE a.id > b.id
   AND a.scout_asset_id = b.scout_asset_id
   AND a.revenue_source = b.revenue_source
   AND a.window_start   = b.window_start
   AND a.window_end     = b.window_end;
DROP INDEX IF EXISTS scout_asset_attribution_dedupe_uidx;
CREATE UNIQUE INDEX IF NOT EXISTS scout_asset_attribution_dedupe_uidx
    ON scout_asset_attribution (scout_asset_id, revenue_source, window_start, window_end);

-- [3] REQUIRED: PostgREST caches the schema; without this reload, upserts keep 42P10-failing
--     even though the indexes exist (verified live, 2026-07-04).
NOTIFY pgrst, 'reload schema';
