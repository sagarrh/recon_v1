# Recon V1 Integration Implementation Status

Updated: 2026-08-01

## Implemented

- Ported Recon into the installable `src/scout` package while retaining the
  untouched imported project as a local reference.
- Added the shared `src/aivc` configuration, contracts, JSON Schemas, database
  audit, bundle producers, composer, persistence repository, parent lifecycle,
  and Typer CLI.
- Corrected cross-client Recon joins, artifact identity, signed flat-baseline
  classification, malformed blog ownership, report association, and outcome
  persistence.
- Added deterministic, boundary-aware company matching in shadow mode for the
  legacy SOV fallback.
- Replaced Recon's LLM citation-analysis branch by default with an exact
  client/cluster/competitor adapter over the canonical citation bundle. The
  legacy branch requires `AIVC_RECON_LEGACY_AI_ANALYSIS_ENABLED=true`.
- Added producer bundles for citation and Recon signals and deterministic
  combined-bundle composition with strict client/checksum validation.
- Added additive `aivc_*` orchestration tables and durable stage/bundle state.
- Moved integrated Slack delivery after required Recon persistence.
- Added installed-wheel-safe migrations, report schema, bundle schemas, and
  Recon prompt resources.
- Moved Recon's historical/manual SQL to `docs/legacy/recon_sql`; it is not
  packaged and cannot be executed by the root migration runner.

## Live verification

- Read-only schema audit: passed.
- `public.ai_monitoring` rows before and after verification: 564.
- Migration `0004_aivc_orchestration.sql`: applied successfully.
- Required command: passed.

```powershell
uv run ai-visibility report generate --company "Aprio"
```

Result: report ID `24ed4ab4-0e1c-4321-9873-f94c9102ce01`, 61 valid runs,
5 invalid runs, and 12 queued pages.

- Citation bundle generation: passed, schema/checksum verified and persisted.
- Citation bundle: 353 signals and 19 comparison evidence records.
- Quality gates: 70 passed, 1 intentionally skipped legacy experimental test;
  Ruff passed; strict mypy passed for 71 source files; wheel/sdist built.

## External credential blocker

The integrated Recon live run is not attempted because these values are not
currently configured:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `OPENROUTER_API_KEY`
- `BRIGHT_DATA_API_KEY`

Slack is also unconfigured but is optional. After configuring the four required
values, run:

```powershell
uv run aivc run --company "Aprio"
```

The command will write the Recon and combined signal bundles beside the
existing citation report and citation bundle.
