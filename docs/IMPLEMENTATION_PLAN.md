# Implementation Plan

## 1. Proposed final project structure

The repository will remain a single Python 3.12+ backend-only package:

```text
.
├── migrations/                 # append-only, non-destructive SQL migrations
├── output/                     # generated reports, ignored except .gitkeep
├── src/ai_visibility/
│   ├── analysis/               # timelines, baselines, competitors, citations
│   ├── attribution/            # evidence scoring and hypothesis selection
│   ├── cli/                    # Typer application and command groups
│   ├── companies/              # client/company resolution and aliases
│   ├── config/                 # validated environment settings and logging
│   ├── database/               # psycopg connection, validation, repositories
│   ├── jobs/                   # persisted queue/checkpoint operations
│   ├── normalization/          # raw run, answer, company, citation normalization
│   ├── reports/                # Pydantic models, builder, persistence, Markdown
│   ├── scraping/               # SSRF-safe retrieval, extraction, snapshots
│   └── utils/                  # text, URL, hashing, and time helpers
├── tests/
│   ├── integration/
│   └── unit/
├── pyproject.toml
├── uv.lock
├── .env.example
└── README.md
```

The existing `docs/`, `fixtures/`, `schemas/`, and `sql/` remain reference
material. No frontend or HTTP API will be added.

## 2. Dependencies and rationale

- Typer: composable CLI command groups.
- Pydantic v2 and pydantic-settings: report contracts and fail-fast settings.
- psycopg 3: direct PostgreSQL transactions and JSON adaptation.
- SQLAlchemy 2: typed SQL composition only where it simplifies repositories.
- httpx and tenacity: bounded, observable page retrieval and retries.
- BeautifulSoup, trafilatura, and pypdf: HTML/PDF content extraction.
- rapidfuzz: guarded alias resolution; deterministic boundary-aware matching
  remains the source of literal metrics.
- numpy/scipy and ruptures: trend statistics and change points when histories
  contain enough observations.
- Jinja2: deterministic Markdown reports.
- structlog: structured operational logs.
- pytest, jsonschema, respx: deterministic, schema, and fetcher tests.
- ruff and mypy: linting, formatting, and strict static checks.

Playwright is an optional dependency/fallback because ordinary HTTP retrieval
must remain the default and browser execution must be isolated.

## 3. Direct PostgreSQL connection design

`DATABASE_URL` is the required database credential. Connections use psycopg 3
with TLS parameters supplied by the URL, bounded connection/statement timeouts,
explicit transactions, JSON decoding, and application naming. Analytical reads
query `public.ai_monitoring` directly. Writes are restricted in application code
to newly created `ai_visibility_*` tables.

The source repository exposes no mutation method for `public.ai_monitoring`.
Database checks are read-only. Normalization transactions use idempotent
upserts keyed by raw run IDs and stable natural keys.

## 4. Migration strategy

Repository-managed SQL migrations will be append-only and recorded in
`public.ai_visibility_schema_migrations`. Migrations may create tables, indexes,
and views whose names start with `ai_visibility_`; they will never alter, drop,
rename, truncate, or attach cascading foreign keys to `public.ai_monitoring`.
Source run IDs are stored as UUID values without a destructive foreign-key
action. Each migration runs transactionally and is safe to rerun.

## 5. Source-table validation

`db check` will verify:

- the connection and current database identity;
- `public.ai_monitoring` exists as a table;
- expected columns exist: `id`, `client_id`, `cluster_id`, `cluster_name`,
  `request_payload`, `answers_list`, `citations_list`, `citations_data`,
  `companies_data`, and `created_at`;
- IDs/timestamps and JSON/JSONB/text-compatible payload columns have usable
  types;
- the application can select a row without writing;
- the normalized schema state is reported separately.

Shape drift will produce a precise diagnostic and no migration or processing.

## 6. Normalization pipeline

For each raw row:

1. Parse JSON values whether returned as native objects or serialized text.
2. Classify zero-answer/zero-citation-group runs as invalid.
3. Build stable monitored-query identity from normalized base query, client,
   provider, method, and an execution-configuration hash; never use `task_id`
   or `cluster_id` alone.
4. Persist one normalized answer per ordinal with a stable hash.
5. Resolve the client and tracked companies from upstream aggregates, aliases,
   and official-domain evidence.
6. Recompute boundary-aware literal answer coverage and total mentions while
   preserving upstream counts, visibility, and word counts.
7. Emit metric mismatch and configuration-completeness flags.
8. Normalize exact URLs by lowercasing the hostname, removing fragments and
   safe tracking parameters, normalizing paths, and retaining meaningful query
   parameters.
9. Persist raw URL occurrences separately from distinct answer coverage and
   classify zero/zero citation positions as unavailable.

All rows use conflict-safe upserts so interruption and reprocessing do not
duplicate facts.

## 7. Historical analysis design

Histories are partitioned by client, stable monitored query, provider, method,
and comparable configuration before aggregation. The engine computes:

- literal and upstream visibility per run;
- adjacent valid-run comparisons;
- rolling 3–5-run mean, median, range, standard deviation, and z-score;
- direction, volatility, spikes, drops, recovery, and new/disappeared states;
- rule-based change points, with ruptures only for sufficiently long series;
- exact-URL raw occurrence and distinct-answer coverage deltas;
- competitor timelines, displacement, and shared recommendation bundles;
- bundle-frequency/Jaccard recommendation-pattern shifts.

Pairwise facts remain evidence units; reports always cover the complete valid
history.

## 8. Job/checkpoint model

Run processing, page fetches, and report generation persist stage/status,
attempt count, error, timestamps, and idempotency keys. Queue claims use
`FOR UPDATE SKIP LOCKED`, bounded retries, and terminal `failed` status.
Synchronous `report generate` may execute stages inline while updating the same
checkpoints. Backfill processes runs chronologically and is safe to resume.

## 9. Page-fetch security model

Only HTTP/HTTPS URLs are accepted. Before every request and redirect, the
fetcher resolves the hostname and rejects loopback, private, link-local,
multicast, reserved, unspecified, and metadata destinations. It limits
redirects, bytes, time, content types, and per-host request rate; applies a
descriptive user agent; respects robots policy; and never executes retrieved
content. HTML extraction strips executable content. PDF extraction is bounded.
Playwright is optional and isolated. Snapshots store extracted text, metadata,
hashes, and errors—not executable page bodies.

Page retrieval is selective: owned/competitor pages, material coverage changes,
high-severity evidence, and missing current snapshots are prioritized.

## 10. Report schema and output flow

Pydantic models mirror
`schemas/company_intelligence_report.schema.json` and retain its extensible
sections. The builder aggregates normalized facts into observed facts,
inferences, confidence, alternatives, evidence, actions, and warnings.
Attribution uses transparent score components and avoids causal wording below
the required evidence gate.

The report is validated against both Pydantic and the JSON Schema, persisted as
JSONB in PostgreSQL, then atomically written to:

```text
output/<company-slug>/company-intelligence-report.json
output/<company-slug>/company-intelligence-report.md
```

Markdown is rendered from the exact validated JSON payload. `report show`
reads the latest local report first and can fall back to the persisted report.

## 11. Test strategy

Unit tests cover JSON coercion, run validity, aliases and substring protection,
URL preservation/removal rules, citation deduplication, position quality,
literal visibility, baselines, trend/change points, bundle detection, SSRF
blocking, extraction, attribution gates, Markdown rendering, and schema
validation.

Fixture tests must reproduce all Aprio expectations, especially 0/20 to 14/21,
the upstream 10/21 mismatch, main-page zero lift/supporting classification, the
one-answer localized article, and the medium-confidence recommendation-pattern
shift. Mocked repository integration tests cover full-history provider
isolation, idempotency, persistence, and partial page failures. Live database
tests are opt-in through `DATABASE_URL`.

Quality gates are `pytest`, `ruff check .`, `mypy src`, JSON Schema validation,
and the final CLI invocation.

## 12. Milestone sequence

1. Scaffold packaging, configuration, logging, and CLI.
2. Add connection/source validation and safe migrations.
3. Implement deterministic normalization and company resolution.
4. Implement histories, comparisons, baselines, citations, and competitors.
5. Generate and validate observation-only reports; pass Aprio fixtures.
6. Add selective secure page retrieval, extraction, snapshots, and diffs.
7. Add attribution scoring, signals, actions, and warnings.
8. Add persistence, backfill, job/retry/status/show commands.
9. Run lint, types, tests, schema checks, and live Aprio generation.
10. Complete setup and operational documentation.

## 13. Assumptions

- `ai_monitoring.client_id` identifies the tenant/client whose entered company
  can be resolved from `companies_data`, answers, and owned citation domains.
- JSON-like columns may be JSON, JSONB, or serialized JSON text.
- `request_payload.base_query`, `service`, and `method` are normally present;
  missing execution controls lower comparability rather than invalidating an
  otherwise non-empty run.
- Official domains can be derived conservatively from owned citations and may
  be augmented in the normalized registry.
- Page-fetch failures do not block an observation report; they become explicit
  warnings.
- The deterministic report does not require `OPENAI_API_KEY`.

## 14. Unresolved risks

- No `DATABASE_URL` is currently present in the environment or repository, so
  live source-column validation, migrations, persistence, and the final Aprio
  run cannot be completed until credentials are supplied.
- The production table may contain schema/type variants not visible in the
  specifications; validation and flexible JSON coercion mitigate this.
- A company-name-to-client mapping outside `ai_monitoring` may exist but is not
  specified. Resolution must remain conservative and report ambiguity.
- Historical execution configuration is incomplete, limiting comparability
  confidence.
- Historical page versions cannot be reconstructed if snapshots were never
  stored.
- Some sites may deny robots access, block retrieval, require JavaScript, or
  return oversized/unsupported content. Reports must preserve the missing
  evidence limitation.
- Database ownership/RLS permissions may permit reads but deny creation of the
  normalized schema; migration failures must identify the exact required grant.
