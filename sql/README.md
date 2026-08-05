# Reference and manual-apply SQL

Nothing in this directory is applied automatically.

Runtime database migrations live in `migrations/` and are packaged under
`src/ai_visibility/resources/migrations/`. Those own only `ai_visibility_*` and `aivc_*`
structures. The active parameterized Recon report query lives at
`src/aivc/resources/sql/recon_report.sql`.

## Reference snapshots

- `0001_reference_schema.sql`, `0002_reference_views.sql`, `0100_analysis_queries.sql` —
  document the original analysis model.
- `agentic_db.sql` — point-in-time snapshot of the live Supabase schema, including the
  Scout write-side tables (`recommendations`, `scout_outcomes`, `scout_assets`,
  `scout_asset_attribution`, `scout_build_briefs`). Useful for checking column types and
  CHECK constraints before changing a writer.

## Manual-apply migrations (`02xx`)

Scout's write-side tables live in Supabase and are **not** managed by this repository's
migration runner. Changes to them ship here as numbered, idempotent, additive SQL that an
operator applies by hand against the Supabase project.

- `0200_recon_revenue_categories.sql` — replaces the SOV-derived revenue estimate with
  evidence-graded revenue categories. **Must be applied before the next live Recon run**;
  without it the recommendation and outcome writers fail on unknown columns.
