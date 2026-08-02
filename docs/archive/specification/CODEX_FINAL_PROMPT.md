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
