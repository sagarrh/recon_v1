# Live Aprio Run Blocker

## Status

The complete offline implementation and quality gates are finished. The live
Supabase stage is blocked because neither the process environment nor a local
`.env` file provides `DATABASE_URL`.

The required command was attempted:

```bash
uv run ai-visibility report generate --company "Aprio"
```

It stopped before making any database connection or write with:

```text
Error: DATABASE_URL is required. Copy .env.example to .env and set the direct
Supabase PostgreSQL connection string.
```

No migrations were applied and no database data was changed.

## Resume

Create `.env` from `.env.example`, set the direct Supabase PostgreSQL
`DATABASE_URL`, then run:

```bash
uv run ai-visibility db check
uv run ai-visibility report generate --company "Aprio"
```

The report command validates `public.ai_monitoring` before applying any
append-only analytical migration. On success it persists normalized evidence,
signals, and the report, then writes:

```text
output/aprio/company-intelligence-report.json
output/aprio/company-intelligence-report.md
```

Optional page-enrichment jobs queued by report generation can be processed
with:

```bash
uv run ai-visibility pages process
uv run ai-visibility report generate --company "Aprio"
```

The second report generation incorporates any available verified snapshots.
