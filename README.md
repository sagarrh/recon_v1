# IMPORTANT: Final Python Backend-Only Scope

This repository initially contains specifications only. Codex must create a Python 3.12+ CLI application in this root. There is no frontend. Read `docs/00_GREENFIELD_PYTHON_BACKEND_ONLY.md` and `CODEX_FINAL_PROMPT.md` first.

---

# AI Visibility Signal Generator — Codex Build Pack

This pack is the source of truth for building a detailed **Company Intelligence Report** from one input:

```json
{ "company_name": "Aprio" }
```

The entered name always represents a **client company**, not an arbitrary competitor.

The system resolves the client, loads all of its valid historical monitoring runs, analyzes every provider/query/cluster over time, inspects client and competitor visibility, compares exact citation URLs, selectively retrieves cited pages, verifies page-level company mentions and content changes, and returns a detailed confidence-scored company intelligence report.

## Read in this order

1. `docs/01_PRODUCT_REQUIREMENTS.md`
2. `docs/02_DOMAIN_CONTEXT_AND_KNOWN_FACTS.md`
3. `docs/03_SYSTEM_ARCHITECTURE.md`
4. `docs/04_DATA_MODEL_AND_SUPABASE.md`
5. `docs/05_PIPELINES_AND_ALGORITHMS.md`
6. `docs/06_ATTRIBUTION_AND_CONFIDENCE.md`
7. `docs/07_REPORT_CONTRACT_AND_API.md`
8. `docs/08_SCRAPING_AND_PAGE_INTELLIGENCE.md`
9. `docs/09_TEST_PLAN_AND_ACCEPTANCE.md`
10. `docs/10_IMPLEMENTATION_ROADMAP.md`
11. `docs/11_SECURITY_OBSERVABILITY_RUNBOOK.md`
12. `docs/12_CODEX_KICKOFF_PROMPT.md`

Supporting material:

- `sql/` — reference SQL and proposed migrations
- `fixtures/` — real Aprio-derived canonical fixtures
- `schemas/` — machine-readable report and API schemas

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
`ai_visibility_*` structures and record checksums. Re-running migrations,
normalization, report generation, and page queueing is idempotent.

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
uv run mypy src
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
