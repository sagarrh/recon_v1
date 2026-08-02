# AI Visibility and Competitive Intelligence Backend

Python 3.12+ backend-only CLI combining AI Citation analysis with Recon competitive
intelligence. There is no frontend.

It builds a detailed client-facing report from one input:

```json
{ "company_name": "Aprio" }
```

The company name always represents a **client**, not an arbitrary competitor.

The system resolves the client, loads all of its valid historical monitoring runs, analyzes every provider/query/cluster over time, inspects client and competitor visibility, compares exact citation URLs, selectively retrieves cited pages, verifies page-level company mentions and content changes, and returns a detailed confidence-scored company intelligence report.

## Project map

- `src/ai_visibility/` — Citation normalization, analysis, page intelligence, and reports.
- `src/scout/` — integrated Recon V1 engine.
- `src/aivc/` — shared contracts, orchestration, and compact report-input preparation.
- `migrations/` — application-managed PostgreSQL migrations.
- `config/` — operator-editable report configuration.
- `schemas/` — public JSON Schema and API contracts.
- `fixtures/` — canonical test evidence.
- `tests/` — unit and Recon integration tests.
- `output/` — ignored generated reports and database backups.
- `docs/` — current guides, architecture, references, plans, and archived history.

Start with [`docs/README.md`](docs/README.md), then follow the
[`Operations Guide`](docs/guides/OPERATIONS_GUIDE.md).

## Non-negotiable product rules

- User input is **client company name only**.
- Analyze the **full available history**, not only one previous/current pair.
- Pairwise comparisons are internal building blocks for trends and change points.
- Compute provider-level and query-level facts before aggregation.
- Preserve upstream metrics, but independently recompute literal answer-level metrics.
- Never equate raw citation occurrences with distinct answer coverage.
- Never claim causation from co-occurrence alone.
- Never claim historical page changes without historical page snapshots.
- Always show confidence, alternatives, evidence, and data-quality warnings.
- Do not send raw monitoring JSON to an LLM and trust it to calculate facts.

## Implemented Python CLI

This repository now contains a Python 3.12+ backend-only CLI. It connects
directly to PostgreSQL; `public.ai_monitoring` is selected as immutable raw
evidence and is never altered.

### Setup

```powershell
uv sync
Copy-Item .env.example .env
```

Set `DATABASE_URL` in `.env` to the direct Supabase PostgreSQL connection
string. `OPENAI_API_KEY` is optional and is not used by deterministic
calculation or report generation.

The integrated Recon producer additionally needs `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, `OPENROUTER_API_KEY`, and (for its web evidence
branches) `BRIGHT_DATA_API_KEY`. LangSmith and Slack credentials are optional.
The direct PostgreSQL URL and Supabase URL must identify the same project.

### Database and processing

```bash
uv run ai-visibility db check
uv run ai-visibility db migrate
uv run ai-visibility runs normalize --run-id <uuid>
uv run ai-visibility runs backfill
uv run ai-visibility runs status
uv run ai-visibility pages process
uv run ai-visibility pages fetch --url "https://example.com/page"
uv run ai-visibility jobs failed
uv run ai-visibility jobs retry
```

Migrations are append-only SQL files in `migrations/`. They create only
`ai_visibility_*` and `aivc_*` structures and record checksums. They never
mutate `public.ai_monitoring`. Re-running migrations,
normalization, report generation, and page queueing is idempotent.

### Integrated citation + Recon pipeline

For the complete operator walkthrough, see the
[`Operations Guide`](docs/guides/OPERATIONS_GUIDE.md).

```powershell
uv run aivc db check
uv run aivc db audit
uv run aivc citations generate --company "Aprio"
uv run aivc run --company "Aprio"
uv run aivc report generate --company "Aprio"
```

`aivc citations generate` runs independently and writes the established
Company Intelligence Report plus `citation-signal-bundle.json`. `aivc run`
resolves the client once, overlaps citation processing with Recon preparation,
feeds the deterministic citation bundle into Recon, persists required Recon
artifacts before optional Slack delivery, and writes:

```text
output/aprio/citation-signal-bundle.json
output/aprio/recon-signal-bundle.json
```

The database remains the complete audit ledger. During report-input preparation,
the application executes the packaged Recon query in a PostgreSQL read-only
transaction and reduces its result plus Citation evidence into compact,
checksummed JSON inputs.

By default, `aivc report generate` resolves the newest complete persisted parent
for the company and does not rerun either producer. Add `--refresh-data` only
when a new Citation + Recon execution is deliberately required.

The application stops at `report-input-snapshot.json`; it does not call an
additional final-report LLM or render final HTML/Markdown. Use the reusable
prompt in `docs/prompts/CLIENT_REPORT_GENERATION_PROMPT.md` with Codex or Claude
Code. NOISE data remains in the database ledger but is excluded from the compact
client-facing input.
The existing citation and Recon reports remain independently usable.
The legacy Recon LLM citation analyzer is off by default and can be
enabled only with `AIVC_RECON_LEGACY_AI_ANALYSIS_ENABLED=true`.

Unscoped backfill deliberately does not guess which tracked company is the
client. Use `runs backfill --company "Aprio"` when client/competitor
relationships and owned domains should be registered. Supabase tenant sessions
derive the client from JWT claims; trusted backend sessions may set
`app.client_id`. The RLS policies added by the migrations deny cross-client
analytical reads.

### Reports

```bash
uv run ai-visibility report generate --company "Aprio"
uv run ai-visibility report show --company "Aprio" --format json
uv run ai-visibility report show --company "Aprio" --format markdown
```

Generation validates the source table, applies safe migrations, normalizes the
complete client history, analyzes provider/query timelines, persists signals
and the report, queues selective page evidence, validates the report schema,
and atomically writes:

```text
output/aprio/company-intelligence-report.json
output/aprio/company-intelligence-report.md
```

Page fetch failures and unavailable historical page snapshots are explicit
warnings; they do not cause invented evidence or prevent an observation report.

Page processing validates and pins public DNS addresses, retries temporary HTTP
failures, caches robots policies briefly, enforces byte/PDF/text limits, resolves
relative links, records extraction-quality metadata, and processes different
domains with bounded concurrency while retaining the per-domain delay. Low-quality
or blocked-page extraction cannot be used as proof that a company is absent or
as direct page-change attribution.

The JavaScript browser fallback is opt-in. Install and enable it only when needed:

```powershell
uv sync --extra browser
uv run playwright install chromium
```

Then set `PAGE_FETCH_BROWSER_FALLBACK_ENABLED=true`. Static HTTP extraction remains
the default. Additional page controls are:

```dotenv
PAGE_FETCH_ROBOTS_CACHE_SECONDS=1800
PAGE_FETCH_MIN_TEXT_CHARACTERS=200
PAGE_FETCH_MAX_EXTRACTED_CHARACTERS=2000000
PAGE_FETCH_MAX_PDF_PAGES=200
PAGE_FETCH_MAX_WORKERS=4
```

### Quality checks

```bash
uv run pytest
uv run ruff check .
uv run mypy
uv run ruff format --check .
```

`pytest` enforces a minimum total coverage threshold of 70%.

### Troubleshooting

- `DATABASE_URL is required`: create `.env` and set the direct PostgreSQL URL.
- `public.ai_monitoring does not exist`: verify the database/project and schema.
- Permission denied during migration: grant the trusted backend role permission
  to create and write the new `ai_visibility_*` tables. No source-table write
  permission is required.
- Failed page jobs: inspect `jobs failed`, correct transient/network policy
  issues, then use `jobs retry` and `pages process`.
- Schema drift: run `db check`; it prints the discovered source columns and
  reports the exact missing or incompatible field.
