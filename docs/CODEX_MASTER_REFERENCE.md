# 00_GREENFIELD_PYTHON_BACKEND_ONLY.md

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


---

# 01_PRODUCT_REQUIREMENTS.md

# Product Requirements Document

## Product name

AI Visibility Signal Generator / Company Intelligence Report

## Product goal

Given only a client company name, generate a comprehensive intelligence report explaining:

- how the company’s AI visibility changed over its full monitoring history
- which providers, queries, and topic clusters drove the changes
- how competitors gained or lost visibility
- which citation sources appeared, disappeared, or gained influence
- whether cited pages actually mention, recommend, compare, or link to the client or competitors
- what likely caused each important change
- how confident the system is
- what the client should do next

## User input

```json
{
  "company_name": "Aprio"
}
```

The entered company is always a client company.

## Primary output

A detailed `company_intelligence_report` containing:

1. Executive summary
2. Full-period trend
3. Provider intelligence
4. Query intelligence
5. Cluster intelligence
6. Company mention quality
7. Citation intelligence
8. Page/content intelligence
9. Competitor intelligence
10. Change-point timeline
11. Confidence-scored signals
12. Alternative explanations
13. Recommended actions
14. Data-quality and comparability warnings

## Product behavior

The system must:

1. Resolve the client company name to a canonical company and client ID.
2. Resolve aliases and the official domain.
3. Load all configured competitors.
4. Load all monitoring history for the client.
5. Exclude invalid and failed runs.
6. Normalize answers, company mentions, and exact citation URLs.
7. Build provider/query-specific time series.
8. Compare adjacent valid runs and rolling baselines.
9. Detect spikes, declines, sustained trends, reversals, and volatility.
10. Identify exact source changes.
11. Prioritize source pages for retrieval.
12. Verify company mentions on those pages.
13. Compare page snapshots when historical snapshots exist.
14. Generate transparent attribution candidates.
15. Produce a detailed report with evidence and confidence.

## Core distinction

The report must separate:

### Observed

Facts calculated directly from stored data.

Example:

> Aprio appeared in 0 of 20 Gemini answers on June 9 and 14 of 21 answers on July 11.

### Inferred

An explanation supported by evidence but not proven.

Example:

> The increase was likely associated with a recommendation-pattern shift and broader source-portfolio change.

### Unknown

Evidence unavailable or insufficient.

Example:

> The historical version of the page was not stored, so a page-content change cannot be confirmed.

## Success criteria

A report is useful when a user can answer:

- Where are we gaining or losing AI visibility?
- Which provider is responsible?
- Which queries changed?
- Which competitors displaced us?
- Which pages are associated with the change?
- Do those pages actually mention us?
- What changed on those pages?
- Is this a sustained trend or one-off fluctuation?
- What should we do next?
- How trustworthy is the explanation?

## Out of scope for the first release

- Proving legal/scientific causation
- Search-engine ranking diagnostics not available in the monitoring data
- Historical page reconstruction when no archived snapshot exists
- Universal company discovery from arbitrary public input
- Fully automated business decisions without human review
- Treating every mention as positive visibility

## User experience

The simplest interface has:

- one client company input
- optional period selector
- Generate Report button
- report status
- detailed report with drill-down evidence

The default period is the full valid history. Optional filters may narrow the report later.

## Report levels

### Company-level

Aggregates all relevant providers, queries, clusters, competitors, and signals.

### Provider-level

Keeps Gemini, OpenAI, Perplexity, AI Overview, and future providers separate.

### Query-level

Analyzes the same monitored query through time.

### Cluster-level

Aggregates related queries after query-level facts are calculated.

### Run-pair level

Internal evidence unit used to identify when a change first occurred.

## Required signal types

- company_visibility_increase
- company_visibility_decrease
- new_company_appearance
- company_disappearance
- competitor_visibility_gain
- competitor_visibility_loss
- provider_divergence
- recommendation_pattern_shift
- owned_source_gain
- owned_source_loss
- third_party_source_gain
- competitor_source_gain
- citation_coverage_increase
- citation_coverage_decrease
- page_content_change
- company_metric_mismatch
- failed_monitoring_run
- insufficient_attribution_evidence


---

# 02_DOMAIN_CONTEXT_AND_KNOWN_FACTS.md

# Domain Context and Known Facts

## Existing source table

The raw source is `public.ai_monitoring` in Supabase/PostgreSQL.

Important fields:

- `id` — run UUID
- `user_id`
- `client_id`
- `cluster_id`
- `cluster_name`
- `request_payload`
- `answers_list`
- `citations_list`
- `citations_data`
- `companies_data`
- `created_at`

Use the raw table as immutable evidence. Build normalized tables around it.

## JSON structure

### `request_payload`

Known fields:

- `service`
- `method`
- `base_query`
- `user_data.task_id`
- `user_data.user_id`
- `user_data.client_id`

### `answers_list`

Array of generated answers.

### `citations_list`

Array aligned by ordinal position with `answers_list`.

```text
answers_list[0] <-> citations_list[0]
answers_list[1] <-> citations_list[1]
```

### `citations_data`

Domain-level aggregate. It is not a reliable exact page count.

### `companies_data`

Upstream company aggregate. Preserve it, but recompute literal company metrics from answer text.

## PostgreSQL/Supabase notes

- The JSON columns may be `json`; cast to `jsonb`.
- PostgreSQL does not provide `jsonb_object_length`.
- Count JSON object keys with `jsonb_object_keys`.
- Use migrations and server-side service-role access for workers.
- RLS must scope all report data to a client.

## Confirmed comparison rules

A comparable time series must preserve:

- client
- stable monitored query
- service/provider
- method
- execution configuration

Do not combine provider timelines before calculating provider-level changes.

`task_id` is execution-specific.

`cluster_id` is too broad to identify the same monitored query.

## Invalid runs

Runs with zero answers and zero citation groups are failed/empty runs.

They must not be interpreted as genuine zero visibility and must not become baselines.

## Citation counting

Store both:

- raw occurrences
- distinct answer coverage

One URL repeated 20 times in one answer contributes:

- 20 raw occurrences
- 1 answer-coverage unit

## Citation positions

Gemini data may contain:

```text
start_index = 0
end_index = 0
```

Treat those positions as zeroed/unusable.

In that situation, claim only:

> The URL and company appear in the same answer.

Do not claim:

> The URL supports the exact sentence mentioning the company.

## URL normalization

Safe default:

- lowercase hostname
- remove fragment
- normalize trailing slash consistently
- preserve meaningful query parameters

Do not collapse:

- YouTube `v` parameters
- CMS `id` or content parameters
- application route parameters

Only remove known tracking parameters when safe.

## Company canonicalization

A company registry and aliases are required.

Examples:

- BDO / BDO USA
- Redstone GCI / Redstone Government Consulting
- PwC / PricewaterhouseCoopers
- EY / Ernst & Young

## Canonical Aprio case

Client ID:

```text
b88e87f3-0aa5-4da9-be48-2807b12d5a91
```

Query:

```text
Who can help me prepare a certificate of cost and pricing data for a federal contract bid?
```

Provider/method:

```text
gemini / moe
```

Previous run:

```text
fb279a6c-f285-4c8a-afce-78469c9455e1
2026-06-09 20:50:11.141433+00
```

Current run:

```text
1ca57705-51b6-47a6-9725-8d43dbb0a937
2026-07-11 20:54:12.98152+00
```

### Literal company result

Previous:

- 20 answers
- Aprio in 0 answers
- literal visibility 0.0

Current:

- 21 answers
- Aprio in 14 answers
- literal visibility 14/21 = 0.6666667

Current Aprio answer numbers:

```text
8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21
```

### Upstream metric mismatch

`companies_data` reported:

- count 10
- visibility 10/21 = 0.4761905

Therefore:

```text
literal count = 14
upstream count = 10
difference = 4
```

The product must emit `company_metric_mismatch`.

### Main Aprio article

URL:

```text
https://www.aprio.com/insights-events/when-does-cost-or-pricing-data-need-to-be-certified-and-what-does-that-mean/
```

Previous:

- raw occurrences: 24
- answer coverage: 17
- Aprio answer co-occurrence: 0
- cited without Aprio: 17

Current:

- raw occurrences: 87
- answer coverage: 21
- Aprio answer co-occurrence: 14
- cited without Aprio: 7

Important interpretation:

The article was already cited in 17 answers when Aprio was mentioned in zero answers. Its presence alone did not cause Aprio to appear.

In the current run, the article is cited in all 21 answers. The Aprio base rate is 14/21, and the URL co-occurrence rate is also 14/21. Therefore the simple association lift is approximately zero.

Classify it as a supporting/topic-context source unless page-diff or stronger evidence establishes more.

### New Aprio article

URL:

```text
https://www.aprio.com/insights-events/how-to-account-for-ai-and-token-costs-on-government-contracts-a-far-dfars-and-cas-compliance-guide-ins-article-gc/
```

Current:

- raw occurrences: 1
- answer coverage: 1
- answer number: 14

It may be a localized contributor to one answer. It cannot explain the broad 14-answer visibility gain.

### Recommendation pattern

Current answers 8–21 repeatedly include a similar group:

- Cherry Bekaert
- Aprio
- CohnReznick
- BDO
- Baker Tilly
- Bennett Thrasher

Current best interpretation:

> Gemini shifted toward repeatedly recommending a common group of GovCon CPA firms. Aprio benefited from this recommendation/source-pattern shift. Its main article was a supporting source, but no single new Aprio page explains the full gain.

Expected overall confidence:

- recommendation/source-pattern shift: medium
- single-page causation: low


---

# 03_SYSTEM_ARCHITECTURE.md

# System Architecture

## High-level flow

```text
Company name
    |
    v
Client/company resolver
    |
    v
Historical run loader
    |
    v
Normalization layer
    |
    v
Provider/query time-series engine
    |
    v
Pairwise + rolling comparison engine
    |
    v
Signal candidate detector
    |
    v
Selective page retrieval
    |
    v
Page/company analysis + page diffs
    |
    v
Attribution/confidence engine
    |
    v
Company Intelligence Report
```

## Main modules

### 1. Company resolver

Input:

```json
{ "company_name": "Aprio" }
```

Responsibilities:

- resolve canonical client company
- resolve `client_id`
- load official domains
- load aliases
- load configured competitors
- reject ambiguous or non-client input

### 2. Run ingestion and normalization

Responsibilities:

- detect unprocessed raw rows
- validate JSON shapes
- resolve stable monitored-query identity
- reject invalid runs
- normalize answers
- recompute literal company mentions
- preserve upstream metrics
- normalize citations
- deduplicate answer/URL pairs
- flag bad citation positions

### 3. Historical analysis engine

Responsibilities:

- build provider/query time series
- compare adjacent valid runs
- compare latest run to rolling baseline
- detect trends
- detect change points
- detect provider divergence
- detect query and cluster concentration

### 4. Signal candidate detector

Responsibilities:

- identify material company changes
- identify competitor gains/losses
- identify source gains/losses
- identify recommendation-pattern shifts
- emit observation-only candidate records

### 5. Page intelligence pipeline

Responsibilities:

- prioritize URLs
- retrieve pages safely
- extract HTML/PDF text
- create snapshots and hashes
- detect company/competitor mentions
- classify mention role
- compare page versions

### 6. Attribution engine

Responsibilities:

- combine observation and page evidence
- score candidate explanations
- choose primary hypothesis
- list alternatives
- assign confidence
- prevent unsupported causal language

### 7. Report generator

Responsibilities:

- aggregate all history
- summarize company trend
- summarize provider/query/cluster performance
- summarize competitors
- construct change timeline
- rank signals
- generate actions
- expose evidence and warnings

### 8. API and UI

Minimum API:

- create or refresh report by company name
- retrieve report
- list report signals
- view signal evidence
- reprocess failed stages

Minimum UI:

- company input
- report summary
- trend sections
- provider/query drill-down
- competitor section
- source evidence
- actions and warnings

## Execution model

Use idempotent jobs.

Recommended fallback when the repository has no worker framework:

- PostgreSQL job table
- server-side worker
- `FOR UPDATE SKIP LOCKED`
- unique idempotency keys
- bounded retries
- dead-letter/failed status

## LLM boundary

LLMs may:

- classify nuanced mention roles
- summarize page changes
- create readable report narratives

LLMs must not:

- calculate visibility
- decide URL status from raw arrays
- select baselines
- determine answer coverage
- silently resolve metric mismatches
- invent historical page versions
- claim causation without structured evidence

All facts must be computed first and passed as structured evidence.


---

# 04_DATA_MODEL_AND_SUPABASE.md

# Data Model and Supabase Design

## Raw evidence

Keep `public.ai_monitoring` immutable.

## Proposed normalized tables

### `monitor_queries`

Stable identity for one monitored query/provider/configuration.

Key columns:

- id
- client_id
- cluster_id
- normalized_base_query
- base_query
- service
- method
- configuration_hash
- configuration_completeness
- timestamps

### `monitor_run_processing`

Tracks normalization and pipeline status for each raw run.

### `monitor_answers`

One row per answer.

Unique:

```text
run_id + answer_number
```

### `companies`

Canonical company.

### `company_aliases`

Aliases used for literal matching and normalization.

### `client_companies`

Maps client company and competitors to a client.

Relationships:

- client
- competitor
- partner
- other_tracked

### `answer_company_mentions`

One company’s appearances in one answer.

Store:

- literal count
- first position
- snippets
- prominence
- mention role
- detection method
- confidence

### `run_company_metrics`

Store both:

- recomputed literal metrics
- upstream `companies_data` metrics
- mismatch flags

### `citation_pages`

One normalized page.

### `answer_citations`

One deduplicated answer/page relationship plus raw occurrence count.

### `run_comparisons`

Previous/current or rolling-baseline comparison.

### `comparison_company_deltas`

Company changes for a comparison.

### `comparison_url_deltas`

Exact URL coverage changes.

### `page_fetch_jobs`

Prioritized retrieval queue.

### `page_snapshots`

Retrieved page versions.

### `page_company_mentions`

Company relationship to one page snapshot.

### `page_snapshot_diffs`

Changes between page versions.

### `signals`

Final structured signal.

### `signal_evidence`

Transparent evidence components.

### `company_intelligence_reports`

Persisted report snapshots.

Suggested columns:

- id
- client_id
- company_id
- analysis_start
- analysis_end
- report_version
- status
- structured_report
- generated_at
- last_error

## Required indexes

At minimum:

- raw monitoring: client_id, created_at
- monitor_queries: client_id, service, method
- monitor_answers: run_id, answer_number
- run_company_metrics: run_id, company_id
- answer_company_mentions: answer_id, company_id
- answer_citations: answer_id, page_id
- comparisons: monitor_query_id, current_run_id
- page jobs: status, run_after, priority
- snapshots: page_id, retrieved_at
- signals: client_id, company_id, created_at, signal_type
- reports: client_id, company_id, generated_at

## RLS

Application users must only access rows belonging to their client.

Workers use service-role credentials server-side only.

Do not expose:

- page bodies across clients
- internal fetch errors to ordinary users
- other tenants’ competitors or reports
- service-role credentials in client code

## Configuration hash

The hash should include all available execution controls:

- service
- method
- model
- model version
- prompt version
- pipeline version
- retrieval configuration
- language
- geography
- generation settings

The current raw payload may not contain all fields. Record configuration completeness and lower comparison confidence when incomplete.

## Migration reference

See:

- `sql/0001_reference_schema.sql`
- `sql/0002_reference_views.sql`

Codex should adapt these to repository conventions instead of blindly applying them.


---

# 05_PIPELINES_AND_ALGORITHMS.md

# Pipelines and Algorithms

## End-product input

```json
{
  "company_name": "Aprio"
}
```

## Report-generation sequence

1. Resolve company to client.
2. Load all valid historical runs for that client.
3. Normalize any unprocessed runs.
4. Build provider/query histories.
5. Compute all adjacent comparisons.
6. Compute rolling baselines.
7. Detect change points and trends.
8. Analyze competitors.
9. Analyze exact citation pages.
10. Retrieve prioritized pages.
11. Analyze page/company relationships.
12. Detect page changes.
13. Score hypotheses.
14. Aggregate into one report.

## Literal company visibility

```text
literal_visibility =
distinct valid answers containing a canonical alias
/
total valid answers
```

Preserve:

- distinct answer count
- total literal mentions
- answer numbers
- context snippets
- upstream count
- upstream visibility

## Baseline selection

### Adjacent valid baseline

For each current run, find the nearest earlier valid run with the same:

- client
- monitor query
- service
- method
- comparable configuration

### Rolling baseline

For report trends, compare the latest valid run against the previous 3–5 comparable valid runs.

Calculate:

- mean
- median
- standard deviation
- min/max
- change from mean
- change from median
- z-score when sample size is adequate

Do not use failed runs.

## Time-series status

Classify:

- increasing
- decreasing
- stable
- volatile
- one-time spike
- one-time drop
- recovering
- newly visible
- disappeared

## Change-point detection

MVP:

- material adjacent delta
- latest value outside prior rolling range
- sustained change in at least two subsequent runs when available

Later:

- statistical change-point method after enough data exists

## Exact URL coverage

Deduplicate one URL per answer.

```text
answer_coverage =
count(distinct answer_number)
```

Keep raw occurrence count separately.

## URL/company association

```text
company_cooccurrence =
answers citing URL and mentioning company
```

```text
cooccurrence_rate =
company_cooccurrence / URL answer coverage
```

```text
company_base_rate =
company answer count / total answers
```

```text
association_lift =
cooccurrence_rate - company_base_rate
```

A ubiquitous source may have high co-occurrence but zero lift.

## Recommendation prominence

Initial deterministic features:

- company in explicit “recommended,” “top,” “examples,” or “who to hire” section
- dedicated bullet or paragraph
- descriptive words near company
- first position in list
- official service description
- generic list mention
- negative/comparative wording

Keep literal visibility and recommendation quality separate.

## Recommendation-pattern shift

For each answer:

1. Extract ordered canonical company set.
2. Group similar sets using Jaccard similarity.
3. Compare bundle frequency over time.

MVP trigger:

- current bundle in at least 40% of answers
- frequency increase at least 25 percentage points

## Competitor analysis

For every configured competitor:

- provider/query visibility timeline
- gain/loss/change point
- source gains
- source losses
- owned source coverage
- third-party mentions
- displacement events
- shared recommendation bundles

## Page prioritization

Queue pages when:

- company visibility changes materially
- URL is new/removed
- URL coverage delta is material
- URL is client- or competitor-owned
- URL strongly co-occurs with changed company answers
- URL is required for a high-severity signal
- recent snapshot is missing

Suggested configurable thresholds:

- visibility delta >= 0.10
- answer count delta >= 2
- URL coverage delta >= 2
- any new/removed owned page
- any new/removed company

## Report aggregation

Aggregate only after provider/query facts exist.

Company-level report metrics should include:

- weighted and unweighted visibility
- provider breakdown
- query breakdown
- cluster breakdown
- latest state
- full-history trend
- largest positive and negative changes
- competitor leaderboard
- source leaderboard
- confidence distribution
- data-quality warnings


---

# 06_ATTRIBUTION_AND_CONFIDENCE.md

# Attribution and Confidence

## Objective

Explain why visibility likely changed without overstating causation.

## Candidate explanation types

- direct_page_content_change
- new_owned_source
- expanded_owned_source
- new_third_party_mention
- expanded_third_party_mention
- competitor_source_gain
- competitor_source_loss
- recommendation_pattern_shift
- broader_source_portfolio_shift
- provider_retrieval_shift
- provider_divergence
- execution_configuration_change
- data_quality_anomaly
- insufficient_evidence

## Evidence gates

A page may be a direct driver only when strong evidence exists, such as:

- historical page diff added or strengthened the company
- a new third-party page meaningfully recommends the company
- a new owned page has material answer coverage
- answer passage semantically aligns with the page’s company content
- pattern repeats across independent comparisons

A page already cited broadly before the company appeared should default to:

- supporting source
- topic-context source
- or unknown contributor

## Initial scoring model

Positive evidence:

- +20 page meaningfully mentions company
- +10 company in title/heading
- +10 links to official domain
- +10 publisher is company
- +20 page diff added/strengthened company
- +10 URL is new or materially expanded
- +10 positive association lift
- +10 answer/page semantic alignment
- +10 repeated across queries/providers/runs

Penalties:

- -25 page does not mention company and is not company-owned
- -20 URL appears in at least 80% of answers
- -15 historical snapshot unavailable
- -10 citation positions unavailable
- -15 configuration comparability incomplete
- -10 only one answer of evidence
- -10 unresolved metric mismatch

Clamp to 0–100.

Suggested bands:

- 70–100 high
- 45–69 medium
- 20–44 low
- 0–19 unrelated/insufficient

Store all components. Do not expose only a black-box score.

## Language policy

High:

> The page change is a likely direct driver.

Medium:

> The source is a likely contributor.

Low:

> The source is associated with the change.

Unknown:

> Evidence is insufficient to determine why.

Never use “caused” for low or medium evidence.

## Alternative explanations

Every material signal should consider:

- provider model or retrieval behavior changed
- prompt or pipeline configuration changed
- source weighting changed
- answer bundle became repetitive
- company extraction changed
- competitor content displaced client content
- page changed but historical snapshot unavailable
- random provider volatility

## Aprio expected classification

Primary:

```text
recommendation_pattern_shift
confidence: medium
```

Related:

```text
expanded_owned_supporting_source
confidence: low-to-medium
```

New AI/token article:

```text
localized_new_owned_source
overall contribution confidence: low
```

Single-page causation:

```text
low
```


---

# 07_REPORT_CONTRACT_AND_API.md

# Report Contract and API

## User-facing input

Only:

```json
{
  "company_name": "Aprio"
}
```

Optional later fields:

- analysis_start
- analysis_end
- force_refresh

Default period:

- full valid history

## Recommended API

### Start or refresh report

```http
POST /api/company-intelligence-reports
```

```json
{
  "company_name": "Aprio"
}
```

Possible response:

```json
{
  "report_id": "uuid",
  "company_name": "Aprio",
  "status": "processing"
}
```

### Retrieve report

```http
GET /api/company-intelligence-reports/{report_id}
```

### Retrieve latest report by client company

```http
GET /api/company-intelligence-reports/latest?company_name=Aprio
```

### Reprocess report

Admin-only:

```http
POST /api/company-intelligence-reports/{report_id}/reprocess
```

## Report top-level sections

```json
{
  "report_type": "company_intelligence_report",
  "company": {},
  "analysis_period": {},
  "executive_summary": {},
  "overall_visibility": {},
  "provider_intelligence": [],
  "query_intelligence": [],
  "cluster_intelligence": [],
  "mention_quality": {},
  "citation_intelligence": {},
  "page_intelligence": {},
  "competitor_intelligence": [],
  "change_timeline": [],
  "signals": [],
  "recommended_actions": [],
  "data_quality_flags": [],
  "methodology": {}
}
```

## Required report details

### Company

- canonical name
- client ID
- official domains
- aliases
- competitor count

### Analysis period

- first valid run
- latest valid run
- valid run count
- invalid run count
- provider count
- query count
- cluster count

### Executive summary

- overall direction
- latest state
- largest positive change
- largest negative change
- strongest provider
- weakest provider
- major competitor movement
- primary hypothesis
- confidence
- top actions

### Provider intelligence

For each provider:

- run count
- latest visibility
- average visibility
- trend
- volatility
- change points
- top queries
- weak queries
- source shifts
- competitor movements

### Query intelligence

For each query:

- provider
- cluster
- run history
- current visibility
- rolling baseline
- trend
- latest delta
- company mention quality
- competitors
- source changes
- signals

### Citation intelligence

- new URLs
- removed URLs
- top answer-coverage gains
- top answer-coverage losses
- owned-source trend
- competitor-owned trend
- third-party trend
- regulatory sources
- raw occurrence warnings

### Page intelligence

- page mentions company
- mention role
- prominence
- official link
- competitor mentions
- content changes
- snapshot availability
- attribution relationship

### Competitor intelligence

- visibility timeline
- provider/query gains
- provider/query losses
- displacement events
- successful owned pages
- successful third-party pages
- recommendation bundle overlap

### Change timeline

Chronological material events across all runs.

### Signals

Each signal includes:

- observed change
- primary hypothesis
- confidence
- evidence
- alternatives
- actions
- warnings

## Machine-readable schema

See:

- `schemas/company_intelligence_report.schema.json`
- `schemas/api.openapi.yaml`


---

# 08_SCRAPING_AND_PAGE_INTELLIGENCE.md

# Scraping and Page Intelligence

## Purpose

Citation data proves that an AI response used a URL. It does not prove that the page meaningfully mentions the client or competitor.

Page retrieval is required for attribution.

## Selective retrieval

Do not fetch every URL on every run.

Prioritize:

1. client-owned new or expanding URLs
2. competitor-owned new or expanding URLs
3. third-party URLs strongly associated with changed company answers
4. pages involved in high-severity signals
5. pages missing a recent snapshot

## Safe fetch requirements

- allow only HTTP/HTTPS
- block private, loopback, link-local, metadata, and internal IPs
- validate DNS and redirected destinations
- limit redirects
- enforce timeout and size limits
- apply per-domain throttling
- use a descriptive user agent
- respect robots and site policies
- use conditional requests
- never execute downloaded code
- sanitize stored/exposed content
- isolate browser fallback

## Extraction order

1. Standard HTTP fetch
2. HTML main-content extraction
3. PDF text extraction
4. Headless browser fallback only when necessary

Store:

- final URL
- canonical URL
- content type
- status
- title
- author
- publish date
- modified date
- main text
- content hash
- structured data
- fetch method
- errors
- HTTP cache validators

## Page/company analysis

For the client and configured competitors, calculate:

- literal mentions
- aliases
- title mentions
- heading mentions
- official-domain links
- publisher ownership
- mention contexts
- mention role
- sentiment
- prominence

Mention roles:

- recommended_provider
- compared_provider
- quoted_expert
- publisher_identity
- incidental_list
- negative_reference
- unrelated
- not_mentioned

## Page snapshots

Create a snapshot when:

- first fetched
- content hash changes
- material metadata changes
- a scheduled refresh requires evidence

## Page diff

Compare current snapshot with previous snapshot.

Detect:

- company added
- company removed
- competitor added
- competitor removed
- recommendation language added
- dedicated section added
- link to official domain added
- title/heading change
- material rewrite
- no meaningful change

## Historical limitation

If no historical snapshot exists, report:

```text
historical_page_version_unavailable
```

Do not infer that the current page content existed historically.

## PDF handling

PDF citations are valid sources.

Extract:

- text
- metadata
- company mentions
- page numbers when available

Keep PDF evidence separate from HTML evidence.


---

# 09_TEST_PLAN_AND_ACCEPTANCE.md

# Test Plan and Acceptance Criteria

## Unit tests

### JSON normalization

- valid arrays
- null arrays
- malformed shapes
- JSON-to-JSONB casts
- object key counting without `jsonb_object_length`

### Company aliases

- Aprio exact match
- BDO and BDO USA canonicalized
- Redstone aliases canonicalized
- false-positive substring protection

### URL normalization

Must not merge distinct:

- YouTube videos
- CMS records with meaningful query parameters

May remove:

- fragments
- known safe tracking parameters

### Citation deduplication

Repeated same URL in one answer:

- one coverage unit
- raw count preserved

### Citation position quality

Zero/zero is `zeroed`, not `valid`.

### Visibility

Literal visibility uses distinct answers, not mention count.

## Integration tests

### Provider isolation

Same query in Gemini and OpenAI creates separate timelines.

### Failed run exclusion

Zero-answer run is invalid and not a baseline.

### Baseline selection

Nearest earlier valid comparable run selected.

### Full-history report

Report includes all valid runs and not only latest pair.

### Metric mismatch

Aprio fixture:

- literal 14
- upstream 10
- mismatch 4
- warning emitted

### Ubiquitous source

Aprio main article:

- current coverage 21
- current co-occurrence 14
- company base rate 14/21
- lift approximately zero

Expected:

- not direct driver
- supporting/topic-context

### Localized source

New Aprio article:

- coverage 1
- co-occurrence 1
- broad company gain 14

Expected:

- localized possible contribution
- low overall contribution

### Recommendation pattern

Similar six-company list repeats in answers 8–21.

Expected:

- recommendation_pattern_shift candidate
- medium confidence when combined with history

### Idempotency

Reprocessing same run does not duplicate:

- answers
- mentions
- citations
- comparisons
- snapshots
- signals
- reports

### RLS

Cross-client report access denied.

## Scraper tests

- redirect limit
- private IP blocked
- response too large
- timeout
- robots denied
- HTML extraction
- PDF extraction
- duplicate content hash
- failed fetch retry
- conditional request

## Report acceptance

Given only:

```json
{ "company_name": "Aprio" }
```

The report must:

- resolve the correct client
- analyze all valid history
- show provider/query-level trends
- report literal/upstream mismatch
- identify July 11 Gemini change point
- avoid single-page causal claim
- identify recommendation-pattern shift as likely primary explanation
- classify the new Aprio article as localized
- show competitors that gained/lost
- expose evidence and warnings
- provide concrete actions

## Quality gate

Before merging:

- migrations pass
- tests pass
- type checks pass
- linting passes
- report schema validates
- fixture report matches expected classification
- no unsupported “caused” language


---

# 10_IMPLEMENTATION_ROADMAP.md

# Implementation Roadmap

## Phase 0 — Repository audit

Codex must inspect:

- backend
- frontend
- Supabase setup
- migrations
- job framework
- API conventions
- tests
- linting
- deployment
- auth/RLS

Output:

- architecture map
- proposed file list
- dependencies
- risks
- deviations

## Phase 1 — Reliable observation layer

Build:

- stable monitor-query identity
- run validation
- answer normalization
- company registry and aliases
- literal mention calculation
- upstream metric preservation
- citation normalization
- exact URL coverage
- baseline selection
- pairwise deltas
- rolling metrics
- data-quality flags

Deliverable:

Observation-only structured report.

No causal explanations yet.

## Phase 2 — Historical signal engine

Build:

- full provider/query time series
- trends
- volatility
- adjacent comparisons
- rolling baselines
- change points
- provider divergence
- recommendation-pattern detection
- competitor timelines

## Phase 3 — Page evidence

Build:

- priority queue
- secure fetcher
- HTML/PDF extraction
- snapshots
- hashes
- page/company analysis
- page diffs

## Phase 4 — Attribution

Build:

- association lift
- page specificity
- page-change evidence
- hypothesis scoring
- alternatives
- confidence
- deterministic narrative fallback

## Phase 5 — Company Intelligence Report

Build:

- report aggregator
- structured report schema
- executive summary
- provider/query/cluster sections
- competitor section
- source/page section
- timeline
- signals
- actions
- methodology

## Phase 6 — API and UI

Build:

- company-name input
- report status
- report retrieval
- drill-down evidence
- filters
- reprocessing controls
- admin health

## Phase 7 — Hardening

Build:

- backfill
- retry policies
- job observability
- RLS tests
- performance indexes
- caching
- score calibration
- production runbook

## Recommended Codex commit sequence

1. docs/repo audit
2. migrations
3. normalization
4. company metrics
5. citations
6. baseline/comparisons
7. full-history trends
8. recommendation patterns
9. page queue/fetcher
10. page analysis/diffs
11. attribution
12. report schema/aggregator
13. API
14. UI
15. RLS/observability/backfill
16. final docs/tests

## Definition of done

- input is company name only
- full valid history analyzed
- provider/query timelines correct
- competitors included
- exact URL coverage correct
- pages selectively retrieved
- page mentions verified
- page diffs used only when available
- signals confidence-scored
- alternatives shown
- actions shown
- mismatch warnings shown
- processing idempotent
- RLS enforced
- tests and fixtures pass


---

# 11_SECURITY_OBSERVABILITY_RUNBOOK.md

# Security, Observability, and Runbook

## Security

### Tenant isolation

All normalized data, signals, and reports must remain client-scoped.

### Worker credentials

Use Supabase service role only on trusted server-side workers.

### SSRF protection

The page fetcher must block:

- localhost
- private ranges
- link-local ranges
- cloud metadata endpoints
- internal DNS results

Revalidate after redirects.

### Content safety

- do not execute scripts
- do not trust MIME type alone
- limit content size
- sanitize HTML
- store text, not executable content
- isolate headless browser

## Job reliability

Every stage must be:

- idempotent
- resumable
- retryable
- observable

Use:

- unique idempotency key
- bounded attempts
- backoff
- `FOR UPDATE SKIP LOCKED`
- failed status
- manual reprocess

## Structured logs

Include:

- client ID
- company ID
- raw run ID
- monitor query ID
- comparison ID
- page ID
- signal ID
- report ID
- stage
- duration
- error category
- retry count

## Metrics

Track:

- raw runs discovered
- valid/invalid runs
- normalized answers
- metric mismatches
- comparisons
- signals
- page jobs
- page success/failure
- extraction failure
- snapshot changes
- confidence distribution
- report generation duration
- report failures

## Backfill

Chronological order:

1. resolve monitor queries
2. normalize runs
3. calculate metrics
4. build comparisons
5. build time series
6. create observation signals
7. queue prioritized current pages
8. mark historical page evidence unavailable
9. generate reports

## Operational commands

Codex should add repository-appropriate commands for:

- normalize one run
- normalize date range
- build one client history
- fetch one page
- reprocess one comparison
- regenerate one report
- backfill all
- list failed jobs
- retry failed jobs

## Failure behavior

The report should still render partial results when:

- some pages cannot be fetched
- historical snapshots are missing
- one provider has invalid runs
- upstream metrics mismatch
- citation positions are unavailable

Failures become warnings, not fabricated evidence.


---

# 12_CODEX_KICKOFF_PROMPT.md

# Final Codex Prompt — Python Backend-Only Company Intelligence Product

This repository is a greenfield standalone Python project.

The current root contains only specifications and reference evidence:

- `docs/`
- `fixtures/`
- `schemas/`
- `sql/`
- `CODEX_MASTER_REFERENCE.md`
- `README.md`

There is no existing application code, frontend, backend, worker, package configuration, or test setup.

Read these files first:

1. `README.md`
2. `docs/00_GREENFIELD_PYTHON_BACKEND_ONLY.md`
3. `CODEX_MASTER_REFERENCE.md`
4. every file under `fixtures/`
5. every file under `schemas/`
6. every file under `sql/`

## Objective

Build a complete working Python 3.12+ command-line product that connects directly to the existing Supabase PostgreSQL database containing:

```text
public.ai_monitoring
```

The only required user input is a client company name:

```bash
uv run ai-visibility report generate --company "Aprio"
```

The application must analyze the client company’s complete valid monitoring history across all providers, monitored queries, clusters, competitors, citations, and available page evidence.

It must generate and persist a detailed Company Intelligence Report and write:

```text
output/aprio/company-intelligence-report.json
output/aprio/company-intelligence-report.md
```

## Technology requirements

Use:

- Python 3.12+
- uv
- Typer
- Pydantic v2
- pydantic-settings
- psycopg 3
- SQLAlchemy 2 where useful
- Alembic or managed SQL migrations
- httpx
- BeautifulSoup
- trafilatura
- pypdf
- Playwright only as a fallback
- rapidfuzz
- numpy/scipy
- ruptures when enough observations exist
- Jinja2
- pytest
- structlog
- tenacity
- ruff
- mypy

Do not build a frontend or HTTP API.

Do not create a monorepo.

## Required first step

Before writing product code, create:

```text
docs/IMPLEMENTATION_PLAN.md
```

It must contain:

1. proposed final project structure
2. dependencies and rationale
3. direct PostgreSQL connection design
4. migration strategy
5. source-table validation
6. normalization pipeline
7. historical analysis design
8. job/checkpoint model
9. page-fetch security model
10. report schema and output flow
11. test strategy
12. milestone sequence
13. assumptions
14. unresolved risks

After writing that plan, continue autonomously. Do not stop after planning.

## Implementation order

1. Scaffold the Python project with `pyproject.toml`, uv, src layout, pytest, ruff, mypy, logging, and environment validation.
2. Implement direct PostgreSQL connectivity using `DATABASE_URL`.
3. Validate the existence and expected shape of `public.ai_monitoring`.
4. Add non-destructive migrations for normalized analytical tables.
5. Implement stable monitored-query identity.
6. Classify valid and invalid runs.
7. Normalize answers.
8. Implement client company resolution, official domains, competitors, and aliases.
9. Recompute literal answer-level company metrics.
10. Preserve upstream `companies_data` metrics.
11. Emit metric mismatch flags.
12. Normalize exact citation URLs without removing meaningful query parameters.
13. Store raw URL occurrences and distinct answer coverage separately.
14. Mark zero/zero citation positions as unavailable.
15. Build provider/query-specific full historical time series.
16. Implement adjacent valid-run comparisons.
17. Implement rolling baselines.
18. Implement trends, volatility, and change-point detection.
19. Implement competitor intelligence.
20. Implement recommendation-pattern detection.
21. Generate an observation-only Company Intelligence Report in JSON and Markdown.
22. Make all Aprio fixtures pass.
23. Implement selective page prioritization.
24. Implement an SSRF-safe HTML/PDF fetcher.
25. Implement page snapshots and content hashes.
26. Verify client and competitor mentions on pages.
27. Implement page-version diffs only when historical snapshots exist.
28. Implement transparent attribution scoring and hypothesis selection.
29. Add confidence, alternative explanations, evidence, and recommended actions.
30. Persist signals and reports in Supabase.
31. Add backfill, retry, failed-job, report-show, and health/status commands.
32. Run a real end-to-end Aprio report.
33. Run tests, ruff, mypy, and report-schema validation.
34. Document setup, commands, migrations, backfill, reprocessing, and operational troubleshooting.

## Required commands

At minimum, provide equivalent commands for:

```bash
uv run ai-visibility db check
uv run ai-visibility db migrate
uv run ai-visibility runs backfill
uv run ai-visibility runs status
uv run ai-visibility pages process
uv run ai-visibility jobs failed
uv run ai-visibility jobs retry
uv run ai-visibility report generate --company "Aprio"
uv run ai-visibility report show --company "Aprio" --format json
uv run ai-visibility report show --company "Aprio" --format markdown
uv run pytest
uv run ruff check .
uv run mypy src
```

## Database safety

Treat `public.ai_monitoring` as immutable and read-only.

Do not:

- alter it
- drop it
- rename it
- delete its rows
- rewrite its JSON
- run destructive resets against the connected Supabase project
- drop or rename any existing tables

Only add new normalized, processing, job, page, signal, and report structures.

All processing must be idempotent and rerunnable.

## Product rules

- Company name is the only required input.
- The company is always a client company.
- Analyze all valid history, not only the latest pair.
- Pairwise comparisons are internal evidence units.
- Keep provider timelines separate before aggregation.
- Exclude failed zero-answer runs.
- Do not use `task_id` as a stable query identifier.
- Do not use only `cluster_id` as a stable query identifier.
- Recompute literal company visibility from answers.
- Preserve upstream company metrics.
- Expose mismatches.
- Count distinct answer coverage separately from raw citation occurrences.
- Preserve meaningful URL query parameters.
- Treat invalid citation positions as unavailable.
- Retrieve pages selectively.
- Verify whether pages actually mention companies.
- Never claim historical page changes without historical snapshots.
- Never treat co-occurrence as proven causation.
- Never ask an LLM to calculate deterministic facts.
- The deterministic product must work without `OPENAI_API_KEY`.
- Always include observed facts, inference, confidence, alternatives, evidence, actions, and warnings.

## Mandatory Aprio fixture expectations

The known Gemini comparison must produce:

```text
previous literal visibility: 0/20
current literal visibility: 14/21
upstream current metric: 10/21
metric mismatch: 4
```

The main Aprio cost/pricing page must not be labeled a proven direct driver because it was already widely cited before Aprio appeared.

The new Aprio AI/token page appeared in only one answer and must be classified as a localized, low-overall-impact candidate.

The primary explanation should be:

```text
recommendation/source-pattern shift
confidence: medium
```

Single-page causation confidence must be low.

## Completion standard

Do not stop after scaffolding.

Continue until:

```bash
uv run ai-visibility report generate --company "Aprio"
```

works end to end, unless blocked by missing database credentials or a genuinely unavailable source-table dependency.

When blocked by credentials:

- document the exact blocker
- complete all code, migrations, fixtures, tests, mocks, and documentation that do not require the secret
- provide the exact command to resume after credentials are added

At completion, provide:

- files created and changed
- migrations added
- commands available
- tests run
- type/lint results
- assumptions
- remaining limitations
- exact steps to generate the Aprio report

