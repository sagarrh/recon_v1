# AIVC Monorepo and Recon V1 Integration - Detailed Implementation Plan

Status: implementation-ready plan; no integration code has been applied yet.

Prepared: 2026-08-01

Imported Recon source: `Scout-Agent-AIVC/`

Related documents:

- `docs/RECON_V1_INTEGRATION_ASSESSMENT.md`
- `docs/POST_REPORT_GSC_GA4_MEASUREMENT_PLAN.md`
- `docs/IMPLEMENTATION_PLAN.md`

## 1. Executive decision

Integrate the projects as a modular monorepo.

The code will live in one repository, use one Python environment and one lock
file, connect to the same Supabase PostgreSQL database, and expose one parent
CLI command. The citation and Recon implementations will remain separate
Python packages with an explicit shared signal contract.

```text
One repository
  |
  +-- ai_visibility package  (canonical citation analysis)
  +-- scout package          (Recon detection/investigation/reporting)
  +-- aivc package           (contracts, orchestration, bundle composition)
```

This gives the operator one command without turning the two codebases into one
tightly coupled module.

Target command:

```powershell
uv run aivc intelligence generate --company "Aprio"
```

Independent commands will remain available:

```powershell
uv run aivc citations generate --company "Aprio"
uv run aivc recon generate --company "Aprio"
uv run aivc bundles compose --company "Aprio"
```

The existing command remains compatible during and after migration:

```powershell
uv run ai-visibility report generate --company "Aprio"
```

## 2. Confirmed requirements

The implementation will satisfy these fixed requirements:

1. Python 3.12+ backend-only application; no frontend work.
2. One repository, one `pyproject.toml`, one `uv.lock`, and one `.env`.
3. Both components use the same Supabase PostgreSQL database.
4. `public.ai_monitoring` remains immutable and read-only.
5. Citation analysis remains independently runnable.
6. Recon remains independently runnable.
7. One orchestrator controls the complete execution sequence.
8. Citation analysis is the canonical AI-citation evidence producer.
9. Recon reuses its existing domain logic where it is correct.
10. Recon's reports and Slack delivery remain supported and enabled through
    configuration.
11. Safe independent work runs partly in parallel to reduce total runtime.
12. Every join and artifact is scoped by exact `client_id`.
13. Both components emit a versioned shared signal bundle.
14. The first integrated milestone produces deterministic combined JSON.
15. Final combined-report composition follows after the combined bundle is
    proven correct.
16. GSC/GA4 growth measurement remains a later, separately gated phase.

## 3. Audit summary of the imported Recon source

The imported critical files match the original Recon V1 copy by SHA-256. The
main pipeline is:

```text
sov_detection
    -> blog_monitoring
    -> merge_triggers
    -> field_resolution
    -> investigation fan-out
         website_diff
         web_intelligence
         ai_response_analysis
         client_readiness
         historical_context
    -> recommendation_generation
    -> report_generation
    -> validation_gate
    -> slack_delivery
    -> persistence from run.py
```

Key characteristics:

- LangGraph owns Recon's graph.
- OpenRouter-backed LLM calls produce investigations, recommendations, and
  reports.
- Bright Data supplies website and SERP evidence.
- Supabase REST reads and writes Recon tables.
- Recon reports and Slack run before `run.py` persists output.
- Scheduled outcome measurement and optional asset attribution run later.
- Optional builders generate client profiles and deployable assets.
- Revenue, GSC, GA4, asset attribution, and builders are feature-gated.

Import observations that must be addressed before code movement:

- `Scout-Agent-AIVC/.git` is a nested repository.
- `Scout-Agent-AIVC/outputs` contains generated artifacts.
- Recon's `.gitignore` ignores `tests/`.
- The local ignored regression tests from the original folder were therefore
  not included in the imported copy.
- The imported Recon virtual environment was correctly not copied.
- The imported migrations include manual, destructive, and legacy SQL that
  must never be fed directly into the current automatic migration runner.

No imported file is deleted during planning. Cleanup occurs only after a root
repository snapshot and explicit path verification.

## 4. Target repository layout

Use this final structure:

```text
project-root/
  pyproject.toml
  uv.lock
  .env.example
  README.md

  src/
    ai_visibility/                 # existing citation package
    scout/                         # ported and hardened Recon package
    aivc/
      __init__.py
      cli/
        app.py                     # parent CLI
        citations.py
        recon.py
        intelligence.py
        bundles.py
        operations.py
      config/
        settings.py                # shared environment settings
      contracts/
        bundle.py
        evidence.py
        signal.py
        recommendation.py
        run.py
      database/
        orchestration.py
        schema_audit.py
      orchestration/
        runner.py
        stages.py
        parallel.py
        recovery.py
      adapters/
        citation_to_recon.py
        citation_bundle.py
        recon_bundle.py
      composition/
        bundles.py
        validation.py

  schemas/
    company_intelligence_report.schema.json
    signal_bundle.schema.json
    combined_signal_bundle.schema.json

  migrations/
    0001_initial.sql
    0002_tenant_rls.sql
    0003_processing_hardening.sql
    0004_aivc_orchestration.sql
    0005_recon_hardening.sql        # only after live schema audit

  tests/
    unit/
    integration/
    contracts/
    tenant_isolation/
    orchestration/
    recon/

  legacy/
    recon_docs/                    # retained reference docs as needed
    recon_migrations/              # never auto-applied

  output/
    <company>/
      company-intelligence-report.json
      company-intelligence-report.md
      citation-signal-bundle.json
      recon-signal-bundle.json
      combined-signal-bundle.json
```

Migration approach for Python packages:

- Preserve `src/ai_visibility` with minimal movement.
- Copy `Scout-Agent-AIVC/scout` to `src/scout` so its existing absolute imports
  continue to work during hardening.
- Create `src/aivc` for shared contracts and orchestration.
- Move Recon scripts into Typer subcommands instead of retaining root scripts
  as the primary interface.
- Keep thin compatibility wrappers temporarily where operationally useful.

## 5. Package and dependency strategy

The root `pyproject.toml` becomes the only project manifest.

### 5.1 Python

- Set `requires-python = ">=3.12"`.
- Recreate the environment with `uv sync`; never copy a `.venv`.
- Delete or archive nested project environments only after path verification.

### 5.2 Runtime dependencies

Retain current citation dependencies and add Recon requirements:

- `langgraph`
- `langsmith`
- `openai`
- `supabase`
- `feedparser`
- `lxml`
- `requests` only where not yet migrated to `httpx`

Do not remove an imported dependency until import analysis and tests prove it
unused.

### 5.3 Development dependencies

Use the current repository's quality toolchain across all three packages:

- pytest and pytest-cov;
- Ruff;
- mypy;
- response/network test doubles;
- JSON Schema validation.

Expand Hatch wheel packages to include:

```toml
packages = ["src/ai_visibility", "src/scout", "src/aivc"]
```

Verify that Recon Markdown prompt files are present in the built wheel. Add
explicit package-data configuration if Hatch does not include them by default.

Treat all runtime data files as package resources, including citation report
schemas, SQL migrations, Recon prompts, and shared contract schemas. Replace
source-tree-relative lookups such as `Path(__file__).parents[...]` with
`importlib.resources` (or an equally install-safe abstraction). Test these
lookups from an installed wheel in a temporary environment, not only from an
editable checkout.

As an early deployment preflight, prove that the CI/production runner can reach
the configured PostgreSQL endpoint. Where Supabase direct database DNS is not
usable from an IPv4-only runner, use the documented session-pooler connection
string without weakening TLS verification or logging credentials.

### 5.4 Console commands

Keep:

```toml
ai-visibility = "ai_visibility.cli.app:main"
```

Add:

```toml
aivc = "aivc.cli.app:main"
```

Do not keep `run.py` as the long-term orchestrator.

## 6. Feature disposition

Recon contains more functionality than the first integrated run should
execute. Use the following disposition.

### 6.1 Enabled in the normal integrated run

| Feature | Treatment |
|---|---|
| AI monitoring normalization and citation analysis | Keep in `ai_visibility`; canonical source |
| SOV detection | Port, harden, enable |
| Blog monitoring | Port, enable using existing flag |
| Trigger merge and field resolution | Port, harden, enable |
| Website-diff investigation | Port, enable |
| Web/SERP intelligence | Port, enable |
| Client readiness | Port, enable |
| Historical context | Port, enable; deep synthesis remains separately gated |
| Recon recommendations | Port, harden, enable |
| Recon internal reports | Port, harden, enable |
| Recon client summaries | Port, harden, enable |
| Validation and numeric provenance | Port, harden, enable |
| Recon persistence | Rewrite unsafe boundary; enable |
| Slack delivery | Preserve and enable through configuration after persistence |
| Citation and Recon bundle export | New, enable |
| Combined bundle composition | New, enable |

### 6.2 Replaced

| Existing feature | Replacement |
|---|---|
| Recon `ai_response_analysis` LLM branch | Adapter over canonical citation bundle |
| Recon AI response cache reads for investigation | Shared citation evidence contract |
| Root `run.py` lifecycle | Durable `aivc` orchestrator |
| Name-based Recon invocation | Resolve once, pass exact `client_id` |
| Index-aligned report persistence | Identity-keyed artifact maps |
| Manual unsafe client-package exporter | Strict versioned bundle exporters |

The legacy AI-response node remains temporarily available behind a default-off
compatibility flag. It is never an automatic fallback because that would mask
missing canonical evidence and create duplicate conclusions.

### 6.3 Preserved as separate commands, not run by default

| Feature | Future command/category |
|---|---|
| Weekly deduplication | `aivc operations weekly-run` |
| Outcome measurement | `aivc recon outcomes measure` |
| Calibration reports | `aivc recon calibration` |
| Facts Recon/client profile builder | `aivc recon profile build` |
| Client gap recommendations | Optional profile/build flow |
| Asset brief/generation/approval/handoff | `aivc assets ...` |
| Asset verification | `aivc assets verify` |
| Attribution reports | Deferred operational commands |
| Quarantine disposition | `aivc recon quarantine ...` |
| Diagnostic and forensic scripts | `aivc operations ...` |

### 6.4 Deferred and forced off in the first integration

- GSC and GA4 revenue reads;
- revenue outcome calculations;
- asset revenue attribution;
- CRM-related attribution;
- automatic asset building;
- Composer-specific manual prompt workflow;
- final cross-producer prose report.

The code is retained, but these flags remain false until their dedicated
implementation phases. This does not affect Recon reports or Slack.

### 6.5 Archived after parity is proven

- nested Recon `.git` metadata;
- generated `outputs/` copied with the import;
- stale `scout/db/schema.sql` reference;
- unsafe `scripts/export_client_package.py` path;
- manual migration instructions superseded by the root migration process;
- duplicate root project manifest and entrypoint.

Archive or remove only after a root commit and successful parity tests.

## 7. Shared configuration

Create one shared `AivcSettings` model. Producer-specific accessors may expose
typed subsets, but every value comes from the root `.env`.

### 7.1 Database settings

- `DATABASE_URL`: direct PostgreSQL connection used by citation,
  orchestration, migrations, and database audits.
- `SUPABASE_URL`: Supabase project URL used by Recon's initial REST adapter.
- `SUPABASE_SERVICE_ROLE_KEY`: one canonical name for the backend service key.
- `SUPABASE_KEY`: temporary deprecated alias accepted with a warning.

Add a startup check that the direct database URL and Supabase URL refer to the
same project where this can be determined safely. Abort on a definite mismatch;
never log credentials.

### 7.2 Command-specific credentials

Do not require every external credential for every command.

- Citation-only commands require `DATABASE_URL`.
- Recon database-only diagnostics require Supabase database credentials.
- Recon investigation/report commands require OpenRouter and Bright Data as
  applicable.
- Slack is attempted only when enabled and webhook configuration is complete.
- Help and offline tests require no secrets.

### 7.3 Configuration hygiene

- Use `SecretStr` for every secret.
- Avoid module-level settings construction.
- Provide cache-reset hooks for tests.
- Validate numeric bounds.
- Namespace new orchestration settings with `AIVC_`.
- Preserve existing Recon defaults unless safety requires a documented change.

New settings should include:

- `AIVC_ORCHESTRATION_ENABLED`;
- `AIVC_MAX_PARALLEL_STAGES`;
- `AIVC_ALLOW_PARTIAL_BUNDLE`;
- `AIVC_RECON_LEGACY_AI_ANALYSIS_ENABLED` (default false);
- `AIVC_DELIVERY_MODE` (`configured`, `disabled`, `force`);
- `AIVC_STAGE_LEASE_SECONDS`;
- `AIVC_BUNDLE_OUTPUT_DIR`.

## 8. Canonical identity resolution

Identity is the most important integration invariant.

### 8.1 Shared model

Create:

```python
class ClientIdentity(BaseModel):
    client_id: UUID
    canonical_name: str
    aliases: list[str]
    official_domains: list[str]
```

### 8.2 Resolution rules

The parent CLI resolves the company once before any child stage.

Preferred lookup order:

1. Exact `client_id`, when supplied.
2. Exact canonical/alias registry match from validated client tables.
3. Existing `ai_visibility_client_companies` registry.
4. The citation project's conservative history-based resolver only for
   bootstrap.

Rules:

- Exact UUID always wins.
- Names must resolve to exactly one client.
- Partial first-match selection is prohibited.
- Ambiguity returns candidate IDs and stops before writes.
- All child functions receive `ClientIdentity`, not a raw company string.
- Recon `load_data` gains a `client_id` parameter and applies it in the database
  query; name filtering is retained only as a compatibility wrapper.

### 8.3 Live schema decision

Before implementation chooses the authoritative registry table, run a read-only
schema/data audit of `clients`, `onboarding`, `ai_monitoring`, and the existing
derived registry. Record column types, UUID consistency, duplicate names, and
domain coverage. The plan assumes one UUID represents the same client across
all tables because the user confirmed both systems share the database; the
audit must prove it.

## 9. Shared signal contract

Implement the versioned `SignalBundle` described in the integration assessment
as strict Pydantic models plus JSON Schema.

### 9.1 Bundle fields

Each bundle contains:

- `schema_version`;
- stable `bundle_id`;
- producer name/version/run ID;
- parent orchestration run ID;
- exact `ClientIdentity`;
- analysis period;
- generation timestamp;
- status (`complete`, `partial`, `failed`);
- signals;
- recommendations;
- evidence registry;
- reports/references where applicable;
- data-quality flags, warnings, and abstentions;
- deterministic checksum.

### 9.2 Signal fields

Each signal includes:

- stable signal ID;
- namespaced type;
- subject company/competitor;
- client ID;
- query, provider, query key, cluster ID, and cluster label;
- observation/comparison timestamps;
- direction;
- previous/current/delta values plus unit;
- confidence;
- evidence references;
- hypotheses;
- recommended actions;
- warnings;
- producer-specific payload.

### 9.3 Contract rules

- Extra fields are rejected at contract boundaries unless explicitly allowed
  under `source_payload`.
- IDs and timestamps use normalized string formats.
- Ratios and percentage points are never interchanged.
- Measured evidence is separate from LLM hypotheses.
- Missing values are not converted to zero.
- Large evidence objects are stored once and referenced by stable IDs.
- The bundle checksum excludes volatile serialization details.
- Both Pydantic and JSON Schema must accept/reject the same fixtures.

## 10. Citation producer changes

The existing report remains intact, but add a producer-level bundle builder.

### 10.1 Preserve existing behavior

- Keep database validation first.
- Keep migrations append-only.
- Keep full-history normalization.
- Keep deterministic mention/citation calculations.
- Keep page queueing and optional enrichment.
- Keep current report JSON/Markdown and persistence.
- Keep current CLI command compatible.

### 10.2 Retain missing identity in comparisons/signals

Add these fields where already derivable:

- `monitor_query_key`;
- `cluster_id`;
- `cluster_label`;
- previous/current timestamps;
- comparison ID or stable key;
- client-owned target URL candidates;
- explicit distinction between client-owned and third-party citation URLs.

### 10.3 Export more than client material signals

Recon investigates competitor triggers. The adapter therefore needs structured
competitor changes even when the client itself did not cross the current report
materiality threshold.

Build the citation bundle from normalized comparison data, not solely from the
final report's `signals` list. Include:

- every comparable adjacent run pair;
- target-client observations;
- competitor deltas;
- citation URL deltas;
- page evidence;
- comparability flags;
- configuration completeness;
- metric mismatch flags.

This bundle can be more complete than the user-facing citation report while
remaining deterministic.

### 10.4 Idempotency

Use stable IDs from client, query key, previous/current run IDs, subject, and
producer version. Re-running against unchanged inputs must produce the same
logical bundle and checksum.

### 10.5 Raw source-of-truth verification

Do not assume that the imported `scout-sync` source code proves a deployed,
fresh raw mirror exists. The database audit must determine:

- whether Recon's `ai_responses` table is transformed and which source fields
  it drops;
- whether a separate raw `ai_monitoring` mirror actually exists, is current,
  and is distinct from the immutable platform source;
- whether both applications truly point at the same Supabase project; and
- whether direct reads from immutable `public.ai_monitoring` remove the need
  for a mirror in the one-database deployment.

Complete citation analysis must never silently use `ai_responses` when required
fields such as `citations_list`, request configuration, or full answer arrays
are absent. Prefer the canonical immutable source in the confirmed same-database
deployment. Any mirror is an explicitly identified, freshness-checked fallback,
not an assumed source of truth. No integration code writes to
`public.ai_monitoring`.

## 11. Canonical citation-to-Recon adapter

Replace Recon's `ai_response_analysis` branch with a deterministic adapter.

### 11.1 New evidence model

Do not force rich citation evidence into the legacy prose-oriented
`AICitationChange` shape. Add a model such as:

```python
class CitationInvestigationEvidence(BaseModel):
    client_id: UUID
    competitor_name: str
    cluster_id: str
    matched_signal_ids: list[str]
    provider_query_changes: list[dict]
    visibility_changes: list[dict]
    citation_source_changes: list[dict]
    page_evidence_refs: list[str]
    confidence: str
    warnings: list[str]
    abstained: bool
    abstention_counts: dict[str, int]
```

Legacy positioning/claims fields may be retained as optional compatibility
fields, but the adapter never invents them.

### 11.2 Matching order

For each Recon trigger:

1. Exact `client_id` is mandatory.
2. Exact cluster ID is preferred.
3. Exact/registered competitor alias is required.
4. Provider/query evidence is included within that cluster.
5. Time windows must overlap or be explicitly labeled historical.
6. No cross-client or bare-cluster fallback is allowed.
7. No match produces a typed abstention, not legacy LLM analysis.

### 11.3 Deterministic numeric ownership

Apply the rule: code owns measured numbers; the LLM owns prose and explicitly
labeled hypotheses.

Coverage, occurrence counts, denominators, deltas, citation-source counts,
co-mention measurements, positions, and confidence inputs are calculated before
any LLM call. The LLM may summarize those values but cannot author, recompute,
or overwrite them. Validate the final structured object by asserting that its
measured fields still equal the deterministic adapter output.

Only numbers in explicitly typed measured-evidence fields enter the numeric
provenance allowlist. Do not recursively authorize arbitrary numbers merely
because they appear inside a schema-free JSONB object or `source_payload`.

### 11.4 Answer-count evidence floors

Replace the legacy citation floor based only on paired weeks with a floor that
also uses the actual number of comparable answers in baseline and current
windows. Paired dates without enough answers are insufficient evidence. Persist
the numerator, denominator, window boundaries, excluded invalid runs, and the
reason for any abstention. Threshold values are configuration/versioned inputs
and are included in provenance.

### 11.5 Recommendation prompt update

Update the standard and deep recommendation prompts to accept the new
structured citation block. Preserve evidence/source labels and clearly tell the
model which values are measured and which are hypotheses.

### 11.6 Legacy compatibility

Keep the old node callable only through:

```text
AIVC_RECON_LEGACY_AI_ANALYSIS_ENABLED=true
```

Default false. Never combine canonical and legacy evidence as though they were
independent corroboration.

## 12. Recon correctness remediation before orchestration

These fixes are mandatory before a full run can be considered trustworthy.

### 12.1 Artifact identity

Add `client_id` to:

- `Recommendation`;
- `InternalReport`;
- `ClientSummary`.

Add `cluster_id` where missing. Populate these fields at object creation; do
not reconstruct them later from names or array indexes.

### 12.2 Cross-client SOV lookup

Replace bare `cluster_id` maps in recommendation/report generation with:

```python
(client_id, cluster_id)
```

### 12.3 Persistence maps

Key investigation, recommendation, report, and outcome maps by:

```python
(client_id, competitor_name, cluster_id)
```

For one-verdict-per-cluster records, key by `(client_id, cluster_id)`.

### 12.4 Report association

Stop pairing recommendations, internal reports, and summaries by list index.
Build explicit identity maps and reject missing or duplicate associations.

### 12.5 AI response client scope

The legacy getter currently queries by cluster without client. It must either:

- accept and filter by `client_id` for explicit legacy operation; or
- be unreachable in the canonical integrated graph.

### 12.6 Persistence result

Replace a flat count dictionary with:

```python
class PersistenceResult(BaseModel):
    counts: dict[str, int]
    failures: dict[str, str]
    required_failures: list[str]
    optional_failures: list[str]
```

The parent run cannot be marked complete when required writes fail.

### 12.7 Idempotent investigations

The current investigation writer performs plain inserts. Audit live constraints
and add an idempotent key/upsert path such as `(run_id, trigger_key)` through a
new additive migration after duplicate analysis.

### 12.8 Export fail-closed behavior

Do not port the existing filter-broadening `pull` helper. Every export query
must retain exact client and run scope, and every returned row must be checked
before serialization.

### 12.9 Model defaults

Replace mutable `{}` and `[]` defaults with Pydantic `default_factory` fields.

### 12.10 State initialization

Initialize every graph state key or mark optional TypedDict fields as
`NotRequired`. Do not depend on missing-key behavior.

### 12.11 Logging and exceptions

- Replace operational `print` calls with structured logging over time.
- Narrow blanket `except Exception` blocks.
- Keep best-effort evidence failures explicit as warnings/abstentions.
- Treat required persistence, identity, schema, and configuration failures as
  fatal.

## 13. Database ownership and migration safety

Both components use the same database, but table ownership stays explicit.

### 13.1 Immutable source

`public.ai_monitoring`:

- read by citation only;
- never inserted, updated, deleted, altered, truncated, or dropped;
- protected by migration scanning and read-only transactions.

### 13.2 Citation-owned tables

Continue using `public.ai_visibility_*` tables.

### 13.3 Recon-owned tables

Continue using existing Recon tables including:

- `cycle_runs`;
- `sov_tracking`;
- `investigation_triggers`;
- `investigations`;
- `recommendations`;
- `reports`;
- `blog_detections`;
- `scout_decision_log`;
- `scout_outcomes`;
- operational cache/prompt tables.

### 13.4 New integration-owned tables

Add:

#### `aivc_pipeline_runs`

- parent run ID;
- client ID and canonical name;
- status (`pending`, `running`, `partial`, `completed`, `failed`);
- requested options;
- component versions;
- start/end timestamps;
- error summary;
- combined bundle ID/checksum.

#### `aivc_pipeline_stages`

- parent run ID;
- stage name and attempt;
- status;
- child run/artifact ID;
- input/output checksums;
- lease owner/time;
- start/end timestamps;
- error type/message;
- uniqueness on parent run, stage, and attempt or a documented equivalent.

#### `aivc_signal_bundles`

- bundle ID;
- parent run ID;
- producer;
- producer run ID;
- client ID;
- schema version;
- analysis period;
- status;
- payload JSONB;
- checksum;
- timestamps;
- unique idempotency key.

#### `aivc_delivery_log`

- parent run ID;
- channel and destination key;
- artifact key;
- delivery status;
- attempt count;
- response metadata without secrets;
- delivered timestamp;
- unique delivery idempotency key.

### 13.5 Imported migration policy

Do not automatically execute any SQL from
`Scout-Agent-AIVC/scout/db/migrations`.

Reasons include:

- `triage_first.sql` deletes duplicate rows after making backup tables;
- `scout_assets_dedupe_fix.sql` updates, deletes, drops, and rebuilds indexes;
- `geo_drop_crm_vendor_tables.sql` drops tables;
- `geo_mirror_ga4_gsc.sql` belongs to a later, currently deferred subsystem;
- migration history is not ordered by numeric versions;
- several files assume manual Supabase SQL-editor execution.

Instead:

1. Run a read-only live schema audit.
2. Compare existing tables, columns, indexes, constraints, and duplicates.
3. Record imported SQL as legacy reference.
4. Write new numbered, append-only root migrations.
5. Separate additive DDL from any cleanup/data migration.
6. Require explicit approval for destructive cleanup.
7. Test migrations against a disposable schema/database before production.

### 13.6 Transaction boundaries

The full run includes external HTTP/LLM work and cannot be one database
transaction. Use:

- one atomic transaction per normalized citation run;
- one atomic transaction for each required Recon persistence group where
  feasible;
- one transaction per bundle;
- durable parent/stage status around external work;
- no long-running transaction held across HTTP or LLM calls.

## 14. Orchestration DAG and partial parallelism

Use bounded partial parallelism. Fully sequential execution wastes time, while
unbounded concurrency would overload the database and OpenRouter.

### 14.1 Target DAG

```text
PRE-FLIGHT
  database/source/schema checks
  exact client resolution
  parent run creation
            |
            +---------------------------------------+
            |                                       |
            v                                       v
  CITATION PIPELINE                       RECON PREPARATION
  normalize/analyze/report                load SOV bundle
  build citation bundle                   detect SOV
                                          blog monitoring
                                          merge triggers
                                          field resolution
            |                                       |
            |                          triggers ready|
            |                                       v
            |                     RECON EVIDENCE FAN-OUT
            |                       website diff
            |                       web intelligence
            |                       client readiness
            |                       historical context
            |                                       |
            +-------------------+-------------------+
                                v
                     CITATION -> RECON ADAPTER
                                |
                                v
                         EVIDENCE BARRIER
                                |
                                v
                    recommendation generation
                                |
                                v
                       Recon report generation
                                |
                                v
                         validation gate
                                |
                                v
                   REQUIRED RECON PERSISTENCE
                                |
                    +-----------+-----------+
                    |                       |
                    v                       v
              Slack delivery          Recon bundle
                    |                       |
                    +-----------+-----------+
                                v
                     combined bundle compose
                                |
                                v
                      parent run completion
```

### 14.2 What runs in parallel

- Citation processing and Recon preparation start after the same client is
  resolved.
- Once Recon triggers exist, website diff, web intelligence, client readiness,
  and historical context run concurrently while citation processing continues.
- Citation adaptation joins only when both trigger scope and citation bundle
  exist.
- Page enrichment may run concurrently with Recon investigations when the user
  explicitly requests enrichment.

### 14.3 What remains sequential

- Client resolution before all child work.
- Recommendation generation after every required/abstained evidence source has
  completed.
- Recon report generation after recommendations.
- Validation after reports.
- Required persistence before Slack.
- Bundle composition after both producer bundles exist.
- Parent completion after required stage outcomes are recorded.

### 14.4 Concurrency controls

- Add a bounded top-level executor; default maximum two major producer stages.
- Keep Recon investigation fan-out bounded independently.
- Preserve the OpenRouter process-wide semaphore, default three LLM calls.
- Do not share a potentially non-thread-safe singleton Supabase client between
  concurrent workers; use worker-local clients or a proven safe pool.
- Keep database writes serialized by stage unless explicitly transaction-safe.
- Keep page workers bounded by their existing worker setting.
- Expose concurrency through validated settings and tests.

### 14.5 Why this is safe and faster

Recon's SOV/blog preparation does not depend on citation report generation.
Most Recon evidence nodes do not depend on canonical citation evidence. Running
those branches concurrently overlaps database, network, and LLM wait time while
preserving a deterministic evidence barrier before recommendations.

## 15. Orchestration state and recovery

### 15.1 Stage statuses

Use:

- `pending`;
- `running`;
- `completed`;
- `partial`;
- `failed`;
- `skipped`.

### 15.2 Required versus optional stages

Required for a complete combined run:

- preflight;
- exact client resolution;
- citation bundle;
- Recon preparation;
- Recon evidence barrier, where explicit abstention counts as completion;
- recommendations;
- reports;
- validation;
- required persistence;
- Recon bundle;
- combined bundle.

Optional/non-fatal by default:

- page queue availability;
- page enrichment unless requested as required;
- Slack delivery;
- LangSmith tracing;
- optional evidence sources that explicitly abstain according to policy.

Slack failure does not erase reports, but it is recorded as a failed delivery,
not silently reported as delivered.

### 15.3 Resume behavior

Add:

```powershell
uv run aivc intelligence resume --run-id <uuid>
```

Resume rules:

- Reuse completed stage outputs only when input and version checksums match.
- Reset expired `running` stage leases.
- Re-run failed stages and their downstream dependents.
- Do not resend Slack if a matching delivery log is complete.
- Allow explicit `--redeliver` for intentional delivery retry.
- Never mark a parent complete from row counts alone; validate bundle presence
  and checksums.

### 15.4 Partial runs

Default full command exits non-zero when a required producer fails. An explicit
`--allow-partial` mode may persist a partial combined bundle for internal
diagnostics. Partial output cannot be labeled a complete report.

## 16. Recon reports and Slack delivery

The user chose to preserve these features.

### 16.1 Reports

- Keep existing internal report and client summary generation.
- Update their models to retain exact identity.
- Preserve deterministic fallbacks.
- Keep validation/quarantine before delivery.
- Persist reports as Recon-owned artifacts.
- Include report references or payloads in the Recon bundle.
- Do not call them the final cross-producer report; that composer is a later
  milestone.

### 16.2 Slack

Move Slack out of the pre-persistence graph tail. Correct sequence:

```text
validate -> persist required artifacts -> deliver Slack
```

This ensures a delivered alert always has a durable run/report record.

Additional requirements:

- send only non-quarantined client-eligible artifacts;
- retain triage digest behavior;
- use idempotency keys to prevent duplicate posts on resume;
- record delivery attempts/status without storing webhook secrets;
- no-op clearly when disabled or unconfigured;
- provide `--no-deliver` for tests/shadow runs;
- preserve environment-controlled production enablement.

## 17. CLI design

### 17.1 Parent CLI

```text
aivc
  db
    check
    audit
    migrate
  citations
    generate
    normalize
    pages
  recon
    generate
    diagnose
    reports
    outcomes
    calibration
    profile
    quarantine
  bundles
    validate
    show
    compose
  intelligence
    generate
    status
    resume
  operations
    weekly-run
```

### 17.2 Full generation command

Proposed options:

```powershell
uv run aivc intelligence generate `
  --company "Aprio" `
  --enrich-pages `
  --delivery-mode configured
```

Support `--client-id` to bypass name ambiguity.

Output summary:

```json
{
  "status": "completed",
  "pipeline_run_id": "uuid",
  "client_id": "uuid",
  "citation_bundle_id": "uuid",
  "recon_bundle_id": "uuid",
  "combined_bundle_id": "uuid",
  "citation_report_id": "uuid",
  "recon_run_id": "uuid",
  "delivery_status": "delivered|disabled|failed",
  "output_paths": {}
}
```

### 17.3 Exit behavior

- Configuration, identity, contract, required persistence, or required stage
  failure returns non-zero.
- Optional page queue or Slack failure is visible in status/output but does not
  corrupt producer results.
- Ambiguous company names stop before writes and list candidate client IDs.

## 18. Detailed implementation phases

Each phase has an explicit completion gate. Do not combine phases merely to
save commits.

### Phase 0 - Safety baseline and inventory

Tasks:

1. Initialize or verify a root Git repository and make a clean baseline commit.
2. Confirm the exact absolute paths before touching nested metadata.
3. Inventory imported source, prompts, scripts, SQL, generated artifacts, and
   documentation.
4. Copy the original ignored Recon regression tests into the root tracked test
   tree as reference tests.
5. Run current citation tests, lint, and type checks unchanged.
6. Create a fresh root environment capable of importing Recon dependencies.
7. Attempt Recon tests and record baseline failures; do not alter assertions to
   hide failures.
8. Run a read-only database schema audit when credentials are available.
9. Capture table/column/index/constraint presence without selecting secrets.
10. Confirm the same client UUID mapping across the two systems.
11. Verify whether `ai_responses` is transformed, whether a raw mirror is
    deployed and fresh, and whether direct immutable `public.ai_monitoring`
    reads make that mirror unnecessary.
12. Prove PostgreSQL connectivity from the intended CI/production network;
    test the Supabase session pooler when the runner is IPv4-only.

Gate:

- Current citation baseline remains green.
- Imported source inventory is complete.
- Recon test failures are understood.
- No database writes have occurred.

### Phase 1 - Monorepo scaffolding

Tasks:

1. Merge runtime/dev dependencies into root `pyproject.toml`.
2. Update `uv.lock`.
3. Port `scout/` to `src/scout/` with prompt files.
4. Create `src/aivc/` skeleton and parent Typer CLI.
5. Replace source-tree-relative migration/schema/prompt lookup with installed
   package-resource lookup.
6. Add package-data verification tests for prompts, migrations, and schemas
   from both an editable install and a built wheel.
7. Add temporary compatibility wrapper for the old Recon entrypoint.
8. Move imported documentation/migrations to clearly marked legacy locations.
9. Exclude generated outputs and nested `.git` from root tracking.
10. Remove nested `.git` only after its history is preserved or intentionally
   discarded and the exact target is verified.

Gate:

- `uv sync` succeeds.
- `uv run aivc --help`, `uv run ai-visibility --help`, and Recon compatibility
  help work without credentials.
- All packages import from the installed wheel and editable environment.

### Phase 2 - Shared settings and database preflight

Tasks:

1. Implement `AivcSettings` and typed producer views.
2. Normalize service-key environment naming.
3. Add same-project consistency checks.
4. Add `aivc db audit` with read-only queries.
5. Add required/optional Recon table capability reporting.
6. Keep source validation and immutable-table enforcement.
7. Add command-specific credential validation.

Gate:

- Offline help/tests need no secrets.
- Citation-only command needs no Recon API keys.
- Database audit cannot mutate any table.
- A definite DB/Supabase project mismatch fails safely.

### Phase 3 - Recon characterization and P0 repairs

Tasks:

1. Track the recovered Recon regression tests.
2. Add missing artifact identity fields.
3. Fix client-scoped SOV lookups.
4. Fix client-scoped persistence maps.
5. Replace index-aligned report association.
6. Return structured persistence failures.
7. Prevent completed lifecycle state on required persistence failure.
8. Make investigations idempotent after schema audit.
9. Replace mutable model defaults.
10. Add exact client-ID loading.
11. Add cross-client tests for every table write and report path.
12. Replace Recon's free-text substring SOV fallback with boundary-aware,
    longest-match mention detection.
13. Count per answer and retain answer coverage separately from raw
    occurrences; do not glue all answers into one counting string.
14. Put the new fallback behind a temporary comparison flag that logs old and
    new results side by side without changing triggers during the shadow run.

Gate:

- Every known P0 regression test passes.
- Two clients sharing cluster and competitor names cannot cross-contaminate.
- Forced required-write failure produces failed/partial, never completed.
- `BDO USA` does not also count as `BDO`, and `Aprio` does not match
  `Apriori`, in tracked tests.
- The shadow flag can compare algorithms without changing production trigger
  selection.

### Phase 4 - Shared contract and orchestration schema

Tasks:

1. Implement contract Pydantic models.
2. Add JSON Schemas and fixtures.
3. Add bundle hashing/versioning.
4. Add root migration `0004_aivc_orchestration.sql`.
5. Add repositories for parent runs, stages, bundles, and deliveries.
6. Test idempotency and migration guards.

Gate:

- Pydantic and JSON Schema validation agree.
- Bundle fixtures are byte-stable.
- Migration contains no mutation of `public.ai_monitoring`.
- Orchestration tables enforce client/run identity.

### Phase 5 - Citation bundle producer

Tasks:

1. Retain query/cluster/time identity in comparisons.
2. Build producer bundle from full comparison data.
3. Include all competitor deltas needed by Recon.
4. Separate client-owned targets from third-party citations.
5. Persist bundle and write it atomically to disk.
6. Add citation-only parent CLI adapter.
7. Select the canonical raw source only after the source/mirror freshness audit;
   fail clearly if required citation columns are unavailable.
8. Persist answer-coverage numerator/denominator separately from raw mention
   occurrences.

Gate:

- Aprio citation bundle validates.
- Same inputs produce the same logical checksum.
- Existing citation report output and command remain compatible.
- A sampled client/cluster/window is hand-checked answer by answer, and the
  computed coverage (for example, 4 of 9) exactly matches the raw answers.

### Phase 6 - Canonical citation adapter in Recon

Tasks:

1. Implement new citation investigation evidence model.
2. Match evidence by client, cluster, competitor, and period.
3. Update Recon state and recommendation prompt inputs.
4. Replace the graph's canonical AI node with the adapter.
5. Keep legacy node default-off.
6. Add exact-match, alias, no-match, incomplete, and cross-client tests.
7. Replace paired-week-only gating with versioned minimum comparable-answer
   counts for both baseline and current windows.
8. Assert after LLM processing that measured numeric fields remain byte-for-byte
   equivalent to deterministic adapter output.
9. Restrict provenance harvesting to typed measured fields rather than every
   number present in arbitrary nested JSON.

Gate:

- Recon recommendations receive canonical deterministic citation evidence.
- No LLM call occurs in the adapter.
- No match becomes an explicit abstention.
- Legacy and canonical evidence are never double-counted.
- The LLM cannot create or modify citation counts, denominators, or deltas.
- Insufficient answer volume yields a typed abstention with observed counts.

### Phase 7 - Recon bundle producer

Tasks:

1. Convert verdicts, triggers, investigations, recommendations, reports,
   validation, abstentions, and delivery eligibility to the shared contract.
2. Preserve producer-specific payloads.
3. Fail closed on client/run mismatch.
4. Persist/write bundle atomically.
5. Add Recon-only CLI path using exact client identity.

Gate:

- Recon bundle validates and round-trips.
- Quarantined artifacts remain auditable but client-ineligible.
- Export never reads GA4/GSC tables in this phase.

### Phase 8 - Parent orchestrator and parallel DAG

Tasks:

1. Implement parent run/stage lifecycle.
2. Implement bounded stage executor.
3. Split Recon preparation/evidence/finalization at stable boundaries.
4. Run citation and Recon preparation concurrently.
5. Run independent Recon evidence nodes concurrently.
6. Implement evidence barrier and deterministic merge.
7. Implement leases, retries, resume, and partial policy.
8. Add structured stage logs and checksums.

Gate:

- Full run uses one resolved client ID.
- Parallel and forced-sequential test runs produce equivalent bundles.
- A failed stage resumes without duplicating completed outputs.
- Concurrency never exceeds configured LLM/page/database bounds.

### Phase 9 - Reports, persistence, and Slack ordering

Tasks:

1. Keep Recon internal/client report generation.
2. Validate and quarantine.
3. Persist all required Recon artifacts.
4. Only then execute Slack delivery.
5. Add delivery idempotency/status.
6. Ensure resume does not duplicate Slack posts.
7. Preserve no-delivery mode for tests and shadow runs.

Gate:

- Every delivered Slack artifact has durable persisted backing.
- Persistence failure prevents delivery.
- Delivery failure is visible and retryable without rerunning analysis.

### Phase 10 - Combined bundle composition

Tasks:

1. Validate citation and Recon bundles independently.
2. Require identical client ID.
3. Check time-period compatibility.
4. Preserve units and evidence provenance.
5. Deduplicate canonical citation evidence from the Recon view.
6. Combine recommendations without silently merging different actions.
7. Persist/write combined JSON atomically.

Gate:

- Aprio produces citation, Recon, and combined bundles.
- Every combined signal points to its producer/evidence.
- No duplicated citation claim appears as independent corroboration.

### Phase 11 - Operational command migration

Tasks:

1. Port weekly guard, outcomes, calibration, diagnostics, quarantine, profiles,
   and assets to Typer subcommands.
2. Preserve existing feature flags.
3. Keep GSC/GA4/revenue flags false.
4. Update scheduler documentation to the new command.
5. Retain compatibility wrappers through one deprecation window.

Gate:

- Existing operational workflows have a documented new command or explicit
  deferred status.
- No script silently disappears.

### Phase 12 - Shadow run, rollout, and cleanup

Tasks:

1. Run citation and Recon independently for Aprio with delivery disabled.
2. Run the orchestrated path sequentially and compare.
3. Run with bounded parallelism and compare again.
4. Review DB row counts, identity keys, bundles, reports, and warnings.
5. With trigger behavior still on the old counter, record old-versus-new
   fallback SOV counts and resulting threshold crossings for Aprio and at least
   one overlapping-name fixture/client.
6. Review every changed trigger caused by the new counter, then explicitly
   enable it; do not combine this cutover with an unrelated threshold change.
7. Enable configured Slack only after persistence/delivery tests.
8. Archive imported project scaffolding and generated outputs.
9. Update the root README and runbook.

Gate:

- Final acceptance criteria in section 25 pass.
- Rollback command/path is documented and tested.

## 19. Testing strategy

### 19.1 Existing citation regression suite

All current tests remain green throughout. Coverage must not fall below the
current configured threshold.

### 19.2 Recovered Recon regression suite

Recover the ignored original tests, then expand them. Do not trust documentation
claims that are contradicted by the current code.

### 19.3 Mandatory tenant-isolation tests

- Two clients share a cluster ID.
- Two clients share competitor and cluster names.
- Two clients have the same display name.
- One company name is ambiguous across client IDs.
- Same run contains multiple clients.
- Citation evidence has the right cluster but wrong client.
- Recon output has the right competitor but wrong client.
- Export query returns a row outside requested scope.

All must fail closed or retain correct ownership.

### 19.4 Contract tests

- Valid citation, Recon, and combined fixtures.
- Missing client ID.
- Invalid units.
- Invalid timestamps.
- Schema-version mismatch.
- Unknown fields outside `source_payload`.
- Duplicate evidence IDs.
- Checksum stability.
- JSON Schema/Pydantic parity.

### 19.5 Orchestration tests

- Successful full DAG.
- Sequential versus parallel equivalence.
- Citation stage fails.
- Recon preparation fails.
- One investigation source abstains.
- Required persistence fails.
- Slack fails after persistence.
- Stage worker lease expires.
- Resume with matching checksums.
- Resume after code/version change invalidates downstream reuse.
- `--allow-partial` behavior.
- Ctrl-C/interruption leaves recoverable status.

### 19.6 External integration tests

Use mocks by default for:

- OpenRouter;
- Bright Data;
- Slack;
- website/feed/sitemap requests.

Live tests require explicit markers and credentials. They must never run in the
default unit suite.

### 19.7 Database tests

- Migration immutability scan.
- Read-only source transactions.
- Schema audit on missing/extra columns.
- Bundle upsert idempotency.
- Recon required-write atomicity.
- Investigation uniqueness.
- Parent/stage lifecycle.
- Cross-client foreign-key mapping.
- No imported destructive migration is auto-discovered.

### 19.8 Slack tests

- Disabled/unconfigured no-op.
- Quarantined artifacts skipped.
- Persistence failure prevents delivery.
- Successful delivery recorded.
- Resume does not duplicate delivery.
- Explicit redelivery works once.

### 19.9 Mention and metric correctness tests

- `BDO USA` produces one longest-match company assignment and does not also
  count `BDO`.
- `Aprio` does not match `Apriori`.
- Punctuation, casing, legal suffixes, registered aliases, and Unicode word
  boundaries have explicit fixtures.
- Coverage counts distinct answers while occurrence count retains repeated
  mentions inside one answer.
- Null, malformed, and zero-answer runs abstain or are excluded as specified.
- A database-backed sample is manually reconciled against every raw answer and
  citation in its baseline/current windows.
- Shadow mode records old/new counts without altering trigger selection.
- No LLM-produced value can enter a typed measured-numeric field.

## 20. Code-quality standards

Apply the root standards to all new and ported code:

- Ruff formatting/linting;
- strict mypy for `aivc` and new adapters;
- progressively type `scout`, with explicit temporary overrides only where
  documented;
- Pydantic `default_factory` for mutable values;
- structured logging;
- narrow exception handling;
- no silent fallback on identity, persistence, schema, or contract errors;
- deterministic pure functions for matching, aggregation, and composition;
- dependency injection for network/LLM/database tests;
- no filesystem writes outside configured output/temp locations;
- atomic JSON/Markdown writes;
- no secrets in logs or exceptions;
- docstrings for public boundaries, not line-by-line narration.

## 21. Security requirements

- Keep `public.ai_monitoring` read-only.
- Verify exact client scope before every child stage and export.
- Never select secret columns unnecessarily.
- Keep GSC/GA4 credential code out of the first integration runtime.
- Treat scraped content and LLM output as untrusted input.
- Preserve the citation scraper's SSRF protections.
- Review Recon's direct feed/sitemap HTTP paths before deploying them on a
  privileged network; reuse shared URL security helpers where compatible.
- Use timeouts, byte/page/row limits, redirect validation, and bounded retries.
- Never log OpenRouter, Bright Data, Slack, Supabase, or database credentials.
- Keep Slack destination identifiers out of bundle payloads where sensitive.
- Validate every LLM structured output before persistence or delivery.
- Preserve numeric provenance and quarantine policy.

## 22. Observability

Every parent run must make the following visible:

- parent and child run IDs;
- exact client ID;
- stage status, attempt, start/end, and duration;
- input/output checksums;
- normalized run counts;
- trigger/verdict/investigation counts;
- evidence abstentions by source;
- recommendation/report/quarantine counts;
- token usage by node/model;
- persistence counts and failures;
- Slack delivery status;
- bundle IDs, versions, checksums, and output paths;
- partial/incomplete reasons.

Logs should use one correlation field: `pipeline_run_id`.

## 23. Rollout strategy

### 23.1 Compatibility first

- Keep old citation CLI working.
- Keep a Recon-only compatibility route.
- Add the parent command without replacing schedules immediately.

### 23.2 Shadow mode

Run the new orchestrator with:

- persistence to new orchestration/bundle tables;
- existing producer persistence as configured;
- Slack disabled to prevent duplicate notifications;
- explicit comparison against independent legacy outputs.

### 23.3 Cutover

Cut over the scheduler only after:

- Aprio parity is approved;
- required-write failure behavior is proven;
- tenant isolation tests pass;
- Slack idempotency is proven;
- resume is proven;
- operations documentation is updated.

### 23.4 Rollback

Rollback means:

- disable `AIVC_ORCHESTRATION_ENABLED`;
- run citation and Recon compatibility commands independently;
- disable new scheduler entry;
- leave additive tables/migrations in place;
- do not drop data during emergency rollback.

## 24. Expected outputs for the Aprio acceptance run

The acceptance run must create:

```text
output/aprio/company-intelligence-report.json
output/aprio/company-intelligence-report.md
output/aprio/citation-signal-bundle.json
output/aprio/recon-signal-bundle.json
output/aprio/combined-signal-bundle.json
```

It must also persist:

- a completed parent pipeline row;
- completed/explicitly partial stage rows;
- citation producer bundle;
- Recon producer bundle;
- combined bundle;
- existing citation report;
- existing Recon reports/recommendations;
- delivery record when Slack is configured.

## 25. Final acceptance criteria

The integration is complete only when all of the following pass:

1. One root Python 3.12+ environment installs from one lock file.
2. Both producer packages and shared orchestration package are independently
   importable.
3. Existing citation command remains compatible.
4. Recon-only command works with an exact client ID.
5. Parent command requires only one operator invocation.
6. Client identity is resolved exactly once and propagated everywhere.
7. `public.ai_monitoring` receives no writes or schema changes.
8. Same-database configuration is verified at startup where possible.
9. Known Recon cross-client bugs are fixed and covered by tracked tests.
10. Required persistence failure cannot produce completed status.
11. Recon reports remain generated, validated, and persisted.
12. Slack remains supported and runs only after required persistence.
13. Slack resume/retry cannot duplicate an already completed delivery.
14. Canonical citation evidence replaces Recon's default legacy AI analysis.
15. No canonical/legacy citation double-counting occurs.
16. Citation and Recon bundles validate against the same versioned contract.
17. Combined bundle requires matching client IDs and compatible periods.
18. Sequential and parallel runs produce equivalent logical bundles.
19. Concurrency is bounded for LLM, DB, page, and external requests.
20. Partial results are labeled partial and never presented as complete.
21. Resume works after an interrupted or failed stage.
22. Imported destructive Recon SQL is not auto-applied.
23. GSC/GA4/revenue reads remain disabled in this implementation phase.
24. Unit, integration, contract, tenant-isolation, lint, type, and migration
    checks pass.
25. The Aprio run produces all expected files and persisted artifacts.
26. Prompts, migrations, and schemas resolve from an installed wheel without
    relying on repository-relative parent paths.
27. Raw `ai_monitoring` versus transformed `ai_responses` selection is verified
    against the live schema and freshness state; no mirror is merely assumed.
28. Boundary-aware mention counting passes overlapping-name and substring tests,
    and its trigger impact is reviewed in shadow mode before activation.
29. Sampled citation coverage and occurrence metrics exactly match a manual
    answer-by-answer calculation.
30. Every report-visible citation number is deterministic, provenance-scoped,
    and immutable across the LLM prose stage.

## 26. Implementation order summary

Use this order without skipping safety gates:

```text
1. Root snapshot + recover tests + DB audit
2. Port Recon package into src/
3. Unify environment and CLI
4. Fix Recon P0 correctness issues
5. Add shared contracts and orchestration tables
6. Add complete citation bundle producer
7. Replace Recon AI branch with citation adapter
8. Add Recon bundle producer
9. Add bounded parent orchestration
10. Reorder persist-before-Slack and add delivery idempotency
11. Compose combined bundle
12. Shadow test Aprio sequentially and in parallel
13. Cut over only after acceptance
14. Implement final composer later
15. Implement GSC/GA4 growth measurement after that
```

This plan intentionally reuses Recon's business logic while replacing unsafe
identity, persistence, export, and orchestration boundaries. It keeps all major
capabilities available without forcing every optional subsystem into the first
production run.
