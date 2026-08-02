# Greenfield Python Backend-Only Final Addendum

## Final architecture decision

This is a new standalone Python project.

There is:

- no frontend
- no web application
- no HTTP API requirement for the first release
- no existing backend
- no existing worker
- no existing package structure

The application connects directly to the existing Supabase PostgreSQL database containing:

```text
public.ai_monitoring
```

The table `public.ai_monitoring` is immutable raw evidence.

The first working product is a Python command-line application and processing engine.

## End-product input

The only required input is the client company name:

```bash
uv run ai-visibility report generate --company "Aprio"
```

The entered company is always the client company.

The system must resolve:

- canonical company
- client ID
- company aliases
- official domains
- configured competitors
- all valid historical monitoring runs
- all providers
- all monitored queries
- all clusters
- all relevant citations and page evidence

## End-product output

The command must:

1. Analyze the complete valid historical timeline.
2. Calculate provider/query/cluster-level visibility.
3. Detect trends, volatility, spikes, declines, and change points.
4. Recompute literal company metrics from answer text.
5. Preserve upstream `companies_data` metrics.
6. Flag metric inconsistencies.
7. Compare exact citation URL answer coverage.
8. Analyze client and competitor movements.
9. Selectively retrieve important cited pages.
10. Verify whether pages actually mention, recommend, compare, or link to companies.
11. Compare page snapshots when historical snapshots exist.
12. Generate transparent attribution candidates.
13. Produce confidence-scored signals.
14. Generate recommended actions.
15. Persist normalized data, page evidence, signals, and reports in Supabase.
16. Write JSON and Markdown reports locally.

Required output files:

```text
output/aprio/company-intelligence-report.json
output/aprio/company-intelligence-report.md
```

## Technology stack

Use:

- Python 3.12 or newer
- `uv` for dependency and environment management
- Typer for CLI commands
- Pydantic v2 for configuration and schemas
- `pydantic-settings` for environment validation
- psycopg 3 for direct PostgreSQL access
- SQLAlchemy 2 where ORM/query composition is useful
- Alembic or repository-managed SQL migrations
- httpx for HTTP retrieval
- BeautifulSoup for parsing
- trafilatura for main-content extraction
- pypdf for PDF extraction
- Playwright only as a JavaScript-rendering fallback
- rapidfuzz for alias support
- numpy/scipy for numerical calculations
- ruptures for change-point detection when enough observations exist
- Jinja2 for Markdown rendering
- pytest for tests
- structlog for structured logging
- tenacity for bounded retries

Prefer SQL for bulk relational/JSON aggregation and Python for orchestration, text analysis, scoring, page processing, and report generation.

Do not make pandas the central application architecture. It may be used selectively for analysis or report preparation.

## Final project structure

Codex should create this structure beside the existing specification folders:

```text
.
├── docs/
├── fixtures/
├── schemas/
├── sql/
├── src/
│   └── ai_visibility/
│       ├── __init__.py
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── app.py
│       │   └── commands/
│       │       ├── db.py
│       │       ├── runs.py
│       │       ├── pages.py
│       │       ├── jobs.py
│       │       └── reports.py
│       ├── config/
│       │   ├── __init__.py
│       │   └── settings.py
│       ├── database/
│       │   ├── __init__.py
│       │   ├── connection.py
│       │   ├── repositories/
│       │   └── migrations.py
│       ├── companies/
│       │   ├── resolver.py
│       │   ├── aliases.py
│       │   └── registry.py
│       ├── normalization/
│       │   ├── runs.py
│       │   ├── answers.py
│       │   ├── companies.py
│       │   └── citations.py
│       ├── analysis/
│       │   ├── visibility.py
│       │   ├── citations.py
│       │   ├── competitors.py
│       │   ├── timelines.py
│       │   ├── baselines.py
│       │   ├── change_points.py
│       │   └── recommendation_patterns.py
│       ├── scraping/
│       │   ├── fetcher.py
│       │   ├── security.py
│       │   ├── html.py
│       │   ├── pdf.py
│       │   ├── browser.py
│       │   └── snapshots.py
│       ├── attribution/
│       │   ├── evidence.py
│       │   ├── scoring.py
│       │   ├── hypotheses.py
│       │   └── confidence.py
│       ├── reports/
│       │   ├── builder.py
│       │   ├── models.py
│       │   ├── markdown.py
│       │   └── persistence.py
│       ├── jobs/
│       │   ├── queue.py
│       │   ├── worker.py
│       │   └── retry.py
│       └── utils/
│           ├── hashing.py
│           ├── urls.py
│           ├── text.py
│           └── time.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── migrations/
├── output/
│   └── .gitkeep
├── scripts/
├── pyproject.toml
├── uv.lock
├── alembic.ini
├── .env.example
├── .gitignore
├── README.md
├── CODEX_MASTER_REFERENCE.md
└── CODEX_FINAL_PROMPT.md
```

Codex may adjust internal module names, but the separation of responsibilities must remain clear.

## Required CLI commands

The exact command grouping may vary, but these capabilities are mandatory:

```bash
uv run ai-visibility db check
uv run ai-visibility db migrate

uv run ai-visibility runs normalize --run-id <uuid>
uv run ai-visibility runs backfill
uv run ai-visibility runs status

uv run ai-visibility pages process
uv run ai-visibility pages fetch --url "<url>"

uv run ai-visibility jobs retry
uv run ai-visibility jobs failed

uv run ai-visibility report generate --company "Aprio"
uv run ai-visibility report show --company "Aprio" --format json
uv run ai-visibility report show --company "Aprio" --format markdown

uv run pytest
uv run ruff check .
uv run mypy src
```

Equivalent commands are acceptable if documented.

## Environment variables

Create `.env.example` with:

```text
DATABASE_URL=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
PIPELINE_VERSION=0.1.0
REPORT_OUTPUT_DIR=./output
PAGE_FETCH_USER_AGENT=AIVisibilityIntelligence/0.1
PAGE_FETCH_TIMEOUT_SECONDS=20
PAGE_FETCH_MAX_BYTES=10000000
PAGE_FETCH_MAX_REDIRECTS=5
OPENAI_API_KEY=
```

`DATABASE_URL` is required.

`OPENAI_API_KEY` is optional.

The deterministic pipeline and report must function without an OpenAI API key.

Never commit real secrets.

## Direct database access

Use direct PostgreSQL access through `DATABASE_URL`.

Do not depend on client-side Supabase REST APIs for analytics.

Use transactions for normalization.

Use PostgreSQL locking and idempotency for job processing.

Do not:

- alter `public.ai_monitoring`
- drop existing tables
- rename existing tables
- delete existing raw rows
- run destructive database resets against the connected Supabase project

New migrations may only create the normalized, processing, page, signal, job, and report structures described by the documentation.

## Processing model

The report command may orchestrate the complete pipeline synchronously for the first version while persisting checkpoints.

It must be safe to interrupt and rerun.

Suggested flow:

```text
report generate
  -> resolve client company
  -> validate connection and source schema
  -> normalize missing historical runs
  -> build provider/query histories
  -> calculate adjacent comparisons
  -> calculate rolling baselines
  -> detect trends/change points
  -> analyze competitors
  -> compare exact citation URLs
  -> prioritize pages
  -> fetch/process required pages
  -> analyze page/company relationships
  -> calculate attribution evidence
  -> generate signals
  -> aggregate company intelligence report
  -> persist report
  -> write JSON
  -> render Markdown
```

Long-running page fetching may use the database job queue.

## First implementation milestone

Build the reliable observation layer before scraping or attribution.

It must include:

- direct database connection
- source-table validation
- stable monitored-query identity
- valid/invalid run classification
- answer normalization
- client/competitor registry
- company aliases
- literal company answer counts
- literal visibility
- upstream metric preservation
- metric mismatch flags
- citation URL normalization
- raw occurrence counts
- distinct answer coverage
- provider/query histories
- adjacent valid-run comparisons
- rolling baselines
- trends
- change points
- competitor changes
- recommendation-pattern candidates
- JSON and Markdown observation-only report
- Aprio acceptance tests

## Mandatory Aprio acceptance facts

The implementation must reproduce:

Previous run:

```text
fb279a6c-f285-4c8a-afce-78469c9455e1
20 answers
Aprio literal answer count: 0
```

Current run:

```text
1ca57705-51b6-47a6-9725-8d43dbb0a937
21 answers
Aprio literal answer count: 14
Aprio literal visibility: 14/21
```

Upstream current metric:

```text
count: 10
visibility: 10/21
```

Required flag:

```text
company_metric_mismatch
difference: 4
```

Main Aprio article:

```text
previous answer coverage: 17
current answer coverage: 21
previous Aprio co-occurrence: 0
current Aprio co-occurrence: 14
```

Interpretation:

```text
supporting/topic-context source
not a proven direct driver
```

New Aprio AI/token article:

```text
current answer coverage: 1
```

Interpretation:

```text
localized possible contribution
low overall contribution
```

Primary event classification:

```text
recommendation/source-pattern shift
confidence: medium
```

Single-page causation confidence:

```text
low
```

## No frontend

Do not create:

- React
- Next.js
- a browser dashboard
- a UI API
- authentication pages
- CSS or visualization work

The CLI, persisted report, JSON file, and Markdown file are the complete first product.

The database design should permit an API or frontend to be added later without redesigning the analytical core.
