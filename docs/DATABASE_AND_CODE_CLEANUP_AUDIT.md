# Database and Code Cleanup Audit

Date: 2026-08-02

## Outcome

The database is not broadly overpopulated by the integration. Most of the large tables belong
to the upstream GEO system, Recon, GSC, or GA4 and remain active. The safe cleanup boundary is
the small `aivc_*` integration layer plus obsolete report modes.

No database rows or objects were changed during this audit. Every inventory query ran inside a
read-only transaction.

## Live database findings

| Object group | Decision | Reason |
| --- | --- | --- |
| `public.ai_monitoring` | Keep, immutable | Upstream Citation source; this project only reads it |
| Recon tables (`clients`, `cycle_runs`, `sov_*`, `investigations`, `recommendations`, `reports`, `scout_*`, and related tables) | Keep | Recon writes these before the report SQL reads them |
| GSC/GA4 tables | Keep, out of scope | Explicitly excluded from this integration cleanup |
| `ai_visibility_companies`, aliases, client mappings, queries, run processing, answers, mentions, citation pages, metrics | Keep | Active Citation normalization and evidence ledger |
| `ai_visibility_page_fetch_jobs`, snapshots, snapshot diffs, page mentions | Keep | Active page-intelligence queue and historical page evidence |
| `ai_visibility_signals`, comparisons, reports | Keep | Active Citation report persistence and audit history |
| `ai_visibility_schema_migrations` | Keep | Required to make migrations safe and repeatable |
| `aivc_pipeline_runs` | Keep table; prune identified test runs | Parent execution ledger and evidence-run resolver |
| `aivc_pipeline_stages` | Keep table; cascade/prune obsolete rows | Failure diagnosis and stage observability |
| `aivc_signal_bundles` | Keep Citation and Scout rows; remove `aivc_combined` rows | The two source ledgers are required; combined rows duplicate them |
| `aivc_final_reports` | Keep table; retain one detailed/client row per parent | Final report persistence is required, but old mode variants are not |
| `aivc_delivery_log` | Drop | Zero rows and no runtime code references |

At audit time the integration contained six Aprio parent runs: one current partial run, one
orphaned running run, three failed development runs, and one superseded partial run. It also
contained ten final-report variants for two parents. Only one report format is now supported.

## Code simplification implemented

- Removed the decision/detailed switch. There is one detailed report configuration.
- Removed the internal/client switch. There is one client-facing renderer.
- Removed the two unused internal report templates.
- Removed combined-bundle composition, schema, artifact, persistence, and publication checks.
- Final reports now use the two authoritative inputs directly: Citation and Scout.
- Recon still runs and persists first; the parameterized read-only Recon report SQL runs after it.
- Final report persistence updates the latest row for a parent instead of creating a new row when
  the config changes.
- Integrated Citation bundle IDs now include the parent run. Persistence no longer moves a bundle
  from one historical parent to another.

## Proposed destructive database cleanup

The review-only script is `sql/aivc_cleanup_20260802_review.sql`. It proposes:

1. Preserve parent `7907efe8-2b44-49cd-bcc9-f205bf460858`.
2. Delete the five superseded, failed, or orphaned AIVC parent runs and their dependent stages.
3. Delete all `aivc_combined` bundle rows and any source bundle attached to a removed parent.
4. Retain only the newest final detailed/client report for the preserved parent.
5. Remove obsolete `compose` and `decision_cards` stage rows from that parent.
6. Drop the empty `aivc_delivery_log` table.
7. Drop the two unused combined-bundle columns from `aivc_pipeline_runs`.
8. Add a uniqueness guard so only one final report may exist per parent.

The script deliberately refuses to run unless the session sets
`aivc.cleanup_approved = 'yes'`. It does not touch `ai_monitoring`, any Recon table, or any
GSC/GA4 table.

## Safe execution order

1. Back up the five `aivc_*` tables with `pg_dump` or a Supabase database backup.
2. Run the updated code once without refreshing evidence:

   ```powershell
   uv run aivc report generate --company "Aprio" --allow-partial
   ```

   This updates the retained report to config version 1.3 and the two-source architecture.

3. Review the cleanup SQL and its fixed UUIDs.
4. Execute it only after explicit approval.
5. Run `uv run aivc db audit` and rerender Aprio once to verify the cleaned state.

## Why no broad truncation is recommended

Truncating `ai_visibility_*` would erase normalized evidence, citation history, page snapshots,
and comparison baselines. Truncating Recon tables would erase the SOV and recommendation data
that `recon_report.sql` needs. Both actions would make the next report smaller by destroying its
history, not by improving the architecture.
