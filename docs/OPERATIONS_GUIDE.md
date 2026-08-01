# AIVC Operations Guide

This guide explains how to configure, run, and operate the backend-only AIVC
project on Windows PowerShell.

## 1. Always run from the repository root

Use this directory:

```text
C:\Users\harso\Desktop\AIVC\ai_visibility_signal_generator_python_final\ai_visibility_signal_generator_python_final
```

Open PowerShell there before running any command:

```powershell
Set-Location "C:\Users\harso\Desktop\AIVC\ai_visibility_signal_generator_python_final\ai_visibility_signal_generator_python_final"
```

Both `ai-visibility` and `aivc` load the `.env` file from this working
directory. Do not configure the old imported `Scout-Agent-AIVC/.env`; that copy
is retained only as reference and is not the integrated runtime.

## 2. One-time environment setup

Install `uv`, then let it create and manage the virtual environment:

```powershell
uv sync
```

You do not need to activate `.venv` manually. Every `uv run ...` command uses
the managed environment automatically.

If `.env` does not exist yet:

```powershell
Copy-Item .env.example .env
```

## 3. Configure the root `.env`

Edit:

```text
C:\Users\harso\Desktop\AIVC\ai_visibility_signal_generator_python_final\ai_visibility_signal_generator_python_final\.env
```

Minimum configuration for citation reports:

```dotenv
DATABASE_URL=postgresql://...
```

Additional configuration for the complete citation + Recon pipeline:

```dotenv
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVICE_ROLE_KEY
OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY
BRIGHT_DATA_API_KEY=YOUR_BRIGHT_DATA_KEY
```

Important rules:

- `DATABASE_URL` and `SUPABASE_URL` must point to the same Supabase project.
- Use a backend service-role key, never an anonymous browser key, for the Recon
  worker.
- Never commit `.env`; it is ignored by Git.
- `OPENAI_API_KEY` is not required by deterministic citation reporting.
- Bright Data is needed for the intended Recon web-evidence workflow. Without
  it, affected evidence branches may abstain or fail.

Optional services:

```dotenv
LANGSMITH_API_KEY=
SLACK_INTEL_WEBHOOK_URL=
SLACK_ALERTS_WEBHOOK_URL=
```

Disable integrated delivery when testing locally:

```dotenv
AIVC_DELIVERY_MODE=disabled
```

Keep the legacy Recon citation LLM disabled:

```dotenv
AIVC_RECON_LEGACY_AI_ANALYSIS_ENABLED=false
```

## 4. Verify configuration and database access

Run these before production work:

```powershell
uv run aivc db check
uv run aivc db audit
```

`db check` validates connectivity, project identity where possible, and the
immutable `public.ai_monitoring` source.

`db audit` performs a read-only inspection of the citation and Recon tables,
client identity overlap, required columns, and mirror freshness. It does not
fetch monitoring data into another database.

## 5. Apply supported migrations

```powershell
uv run ai-visibility db migrate
```

The root migration runner applies checksummed, additive migrations for
`ai_visibility_*` and `aivc_*` tables. It refuses SQL that mutates
`public.ai_monitoring`.

Historical Recon SQL under `docs/legacy/recon_sql/` is reference-only. Never
apply that directory automatically.

## 6. Generate a citation report

```powershell
uv run ai-visibility report generate --company "Aprio"
```

This creates and persists:

```text
output/aprio/company-intelligence-report.json
output/aprio/company-intelligence-report.md
```

It also queues selected cited pages, but it does not fetch them during this
command.

To create the citation report and its shared signal bundle together:

```powershell
uv run aivc citations generate --company "Aprio"
```

This additionally creates and persists:

```text
output/aprio/citation-signal-bundle.json
```

## 7. Include scraped-page evidence

Use this three-command workflow:

```powershell
# Generate once and queue relevant pages.
uv run aivc citations generate --company "Aprio"

# Fetch and analyze queued pages.
uv run ai-visibility pages process --limit 20

# Regenerate from the newly stored page evidence.
uv run aivc citations generate --company "Aprio"
```

Increase `--limit` if more than 20 jobs are pending. Re-running is safe and
updates the idempotent report/bundle instead of intentionally creating
duplicates.

The first successful page snapshot proves what the page currently contains.
A later snapshot is required before the system can claim that the page itself
changed over time.

Useful page/job commands:

```powershell
uv run ai-visibility jobs failed
uv run ai-visibility jobs retry
uv run ai-visibility pages process --limit 20
```

## 8. Run the integrated citation + Recon pipeline

After all Recon credentials are configured:

```powershell
uv run aivc run --company "Aprio"
```

The command:

1. Resolves Aprio to one exact client UUID.
2. Runs citation processing while preparing Recon data.
3. Produces the deterministic citation bundle.
4. Supplies matching citation evidence to Recon by exact client, cluster, and
   competitor identity.
5. Generates Recon investigations, recommendations, and reports.
6. Validates and persists required Recon artifacts.
7. Delivers optional Slack messages only after persistence.
8. Produces and persists the Recon and combined bundles.

Expected files:

```text
output/aprio/company-intelligence-report.json
output/aprio/company-intelligence-report.md
output/aprio/citation-signal-bundle.json
output/aprio/recon-signal-bundle.json
output/aprio/combined-signal-bundle.json
```

The combined bundle is the full structured audit source. For the unified,
client-facing report, use the next section.

## 9. Generate the unified final report

The normal operator command runs Citation and Recon once under a single parent
run and then creates the final JSON, Markdown, and HTML report:

```powershell
uv run aivc report generate --company "Aprio" --profile decision
```

For production, prefer the exact authoritative UUID. This avoids any ambiguity
when two database clients share the same display name:

```powershell
uv run aivc report generate --client-id "CLIENT-UUID" --profile decision
```

Use `--profile detailed` for full SOV company tables and history, query-level
visibility, expanded citation and Recon findings, recommendations, run history,
methodology, and evidence appendices. The decision profile deliberately keeps a
smaller rendered view. The underlying measurements and publication filters are
identical in both profiles.

Final-report generation runs a packaged, parameterized version of
`recon_query_for_report.sql` using the exact client UUID, reporting week, and
configured history window. That query is executed after `SET TRANSACTION READ
ONLY`; it fetches data and cannot modify the database. The complete result is
stored as `recon_reporting` in the structured JSON snapshot. HTML and Markdown
render useful sections from that payload rather than dumping the raw object.
NOISE-classified rows remain in the structured payload for audit but are not
shown as client findings.

The default profile is controlled by `config/reporting.toml`, or by:

```dotenv
AIVC_REPORT_PROFILE=decision
AIVC_REPORT_CONFIG_PATH=
```

Partial reports are truthful but have disclosed evidence limitations. They are
kept run-scoped and persisted; latest convenience copies are refreshed only
when the report is complete, or when `--allow-partial` is explicitly supplied.

Expected additional files:

```text
output/aprio/final-report.json
output/aprio/final-report.md
output/aprio/final-report.html
output/aprio/runs/<parent-run-id>/<profile>/artifact-manifest.json
output/aprio/runs/<parent-run-id>/<profile>/final-report.json
output/aprio/runs/<parent-run-id>/<profile>/final-report.md
output/aprio/runs/<parent-run-id>/<profile>/final-report.html
```

Inspect, validate, or rerender an exact historical parent without rerunning
either producer:

```powershell
uv run aivc report show --parent-run-id "PARENT-UUID"
uv run aivc report validate --path "output/aprio/final-report.json"
uv run aivc report render --parent-run-id "PARENT-UUID" --profile detailed --allow-partial
```

Historical rendering always reads the Citation and producer bundles attached to
that parent run. If the parent already has a schema 1.1 report snapshot, it also
reuses that snapshot's exact full Recon reporting payload. For an older parent
that predates schema 1.1, the renderer performs one read-only reconstruction
bounded to the parent's reporting week, then persists it for deterministic
future rerenders.

## 10. Database safety

`public.ai_monitoring` is immutable source evidence. Application code reads it
inside read-only operations and never inserts, updates, deletes, truncates,
alters, or drops it.

Normal operation does write derived data to:

- `ai_visibility_*` citation analysis/report tables;
- existing Recon-owned tables such as `cycle_runs`, `investigations`,
  `recommendations`, and `reports`;
- `aivc_*` parent-run, stage, bundle, and delivery tables.

## 10. Quality checks after code changes

```powershell
uv run ruff check .
uv run mypy
uv run pytest -q
uv build
```

Expected current baseline: 70 passing tests and one intentionally skipped
legacy experimental test.

## 11. Common failures

### Hostname cannot be resolved

```text
failed to resolve host ... getaddrinfo failed
```

Check internet/DNS access and confirm the Supabase hostname in `DATABASE_URL`.

### Different Supabase projects

Ensure the project reference in `DATABASE_URL` matches the host in
`SUPABASE_URL`.

### Missing Recon credentials

Configure `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `OPENROUTER_API_KEY`,
and `BRIGHT_DATA_API_KEY` in the root `.env`.

### Page jobs fail

Inspect failed jobs, retry transient failures, and process again. Robots rules,
private-network protection, size limits, or unsupported content may
legitimately prevent a fetch.

### Report is marked partial

Partial does not necessarily mean report generation failed. It means the
report contains explicit limitations such as incomplete provider
configuration, unavailable citation positions, metric mismatches, or missing
historical page snapshots.
