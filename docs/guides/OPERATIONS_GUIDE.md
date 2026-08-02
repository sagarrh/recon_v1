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
directory. Recon runs from the integrated `src/scout` package, so there is no
second Recon project or second `.env` to configure.

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
8. Persists the Citation and Recon source bundles under the same parent run.

Expected files:

```text
output/aprio/company-intelligence-report.json
output/aprio/company-intelligence-report.md
output/aprio/citation-signal-bundle.json
output/aprio/recon-signal-bundle.json
```

The two producer bundles are the structured integration ledger. They are not
duplicated into a third combined bundle. For the unified client-facing report,
use the next section.

## 9. Generate the detailed client report

For a fresh reporting cycle, use `--refresh-data`. The ordering is enforced:
AI Citations runs, Recon runs and persists its client data, and only then does
the report stage execute the read-only Recon reporting SQL.

```powershell
uv run aivc report generate --company "Aprio" --refresh-data --allow-partial
```

To regenerate presentation from an already completed Citation + Recon parent,
omit `--refresh-data`:

```powershell
uv run aivc report generate --company "Aprio" --allow-partial
```

For exact reproducibility, prefer `--parent-run-id`. For exact client selection,
prefer `--client-id`; a duplicated company name fails rather than guessing.

The public CLI now produces one product: a detailed client-facing report. The
former decision/internal combinations are no longer CLI options.

Final-report generation runs the packaged, parameterized Recon report query
using the exact client UUID, reporting week, and
configured history window. That query is executed after `SET TRANSACTION READ
ONLY`; it fetches data and cannot modify the database. The complete source
records remain in the database ledger. Only compact, report-ready Citation and
Recon inputs are supplied to the narrative model. NOISE-classified rows remain
auditable in the ledger but are not shown as client findings.

Narrative generation uses the configured Recon/OpenRouter credentials. These
optional controls are available:

```dotenv
AIVC_REPORT_CONFIG_PATH=
AIVC_REPORT_LLM_MODEL=google/gemini-2.5-flash-lite
AIVC_REPORT_LLM_MAX_TOKENS=12000
AIVC_REPORT_LLM_REQUIRED=false
```

The default uses Recon's existing lightweight summarization model. An empty
model value reuses Recon's configured synthesis model. With
`AIVC_REPORT_LLM_REQUIRED=false`, an unavailable LLM produces a disclosed,
deterministic narrative fallback instead of losing the report.

Partial reports are truthful but have disclosed evidence limitations. They are
kept run-scoped and persisted; latest convenience copies are refreshed only
when the report is complete, or when `--allow-partial` is explicitly supplied.

Expected additional files:

```text
output/aprio/final-report.json
output/aprio/final-report.md
output/aprio/final-report.html
output/aprio/final-report-content.json
output/aprio/inputs/ai-citation-report-input.json
output/aprio/inputs/recon-report-input.json
output/aprio/inputs/report-input-snapshot.json
output/aprio/runs/<parent-run-id>/artifact-manifest.json
output/aprio/runs/<parent-run-id>/final-report.json
output/aprio/runs/<parent-run-id>/final-report.md
output/aprio/runs/<parent-run-id>/final-report.html
```

Inspect, validate, or rerender an exact historical parent without rerunning
either producer:

```powershell
uv run aivc report show --parent-run-id "PARENT-UUID"
uv run aivc report validate --path "output/aprio/final-report.json"
uv run aivc report render --parent-run-id "PARENT-UUID" --allow-partial
```

Historical rendering always reads the Citation and producer bundles attached to
that parent run and executes a read-only Recon reconstruction bounded to that
parent's reporting week. It never reruns Recon unless `--refresh-data` is
explicitly supplied.

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
