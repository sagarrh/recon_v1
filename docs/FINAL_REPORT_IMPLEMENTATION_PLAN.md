# Unified AIVC Final Report Implementation Plan

Status: implementation handoff  
Prepared: 2026-08-02  
Target runtime: Python 3.12+ backend-only CLI  
Primary command after implementation:

```powershell
uv run aivc report generate --company "Aprio" --profile decision
```

## 1. Purpose

Implement a reproducible final-report layer over the merged AI Citation Analysis
and Recon pipelines. The final report must answer five client questions:

1. Where is the client visible or absent in monitored AI answers?
2. What materially changed during the reporting period?
3. Which competitors are gaining or losing, and on which topics?
4. What collected evidence plausibly explains the movement?
5. What should the client do next, and how will success be measured?

The implementation must replace the former manual sequence of running
`recon_query_for_report.sql`, copying JSON into an LLM, and asking the LLM to
produce a one-off HTML artifact.

The system must preserve complete machine evidence while producing a much
smaller, client-facing report. It must support two report profiles controlled
by one configuration value or one CLI override:

- `decision`: concise executive/client report;
- `detailed`: expanded analyst report with appendices.

Both profiles must use the same underlying analysis, publication gates, metric
definitions, and evidence. A profile changes selection depth and presentation
only.

## 2. Required reading before implementation

The implementing agent must read these files before editing code:

- `README.md`
- `docs/OPERATIONS_GUIDE.md`
- `docs/INTEGRATION_IMPLEMENTATION_STATUS.md`
- `docs/MONOREPO_RECON_INTEGRATION_IMPLEMENTATION_PLAN.md`
- `agentic_db.sql`
- `edge_function.ts`
- `recon_query_for_report.sql`
- `report_jaggaer.html`
- all root migrations and packaged copies under
  `src/ai_visibility/resources/migrations/`
- current contracts, bundle producers, orchestration, persistence, Recon
  graph, Recon writer, and report code under `src/aivc`, `src/ai_visibility`,
  and `src/scout`

The four root reference files listed above are currently user-supplied files.
Preserve them exactly unless the user explicitly asks to modify or relocate
them. They are inputs and references, not migration files.

## 3. Non-goals

This implementation must not:

- modify, insert into, update, delete from, truncate, alter, or drop
  `public.ai_monitoring`;
- redesign the GEO upstream ingestion system;
- implement GSC or GA4 reporting;
- remove or truncate existing Recon history;
- replace the full citation, Recon, or combined signal bundles;
- pass the complete combined bundle directly to an LLM;
- ask an LLM to generate arbitrary HTML or calculate report metrics;
- combine Recon SOV and literal citation visibility into one synthetic score;
- automatically deliver a client report externally unless delivery is
  explicitly enabled and the report passes publication validation;
- apply files under `docs/legacy/recon_sql/`;
- silently exclude severe data-quality problems merely to make a report look
  complete.

## 4. Current system and confirmed constraints

### 4.1 Current data flow

```text
GEO platform
  -> deployed Edge Function / synchronization owner
  -> Agentic Supabase tables and immutable public.ai_monitoring mirror
  -> AI Citation Analysis reads public.ai_monitoring
  -> Recon reads prepared client/SOV data and writes Recon artifacts
  -> AIVC persists producer bundles and a combined bundle
```

The application treats `public.ai_monitoring` as immutable source evidence.
The separately deployed ingestion owner may populate that table; this project
does not.

### 4.2 Current report artifacts

For Aprio, the present outputs are approximately:

- citation bundle: 2.29 MB, 353 signals;
- Recon bundle: 56 KB, 12 signals;
- combined bundle: 2.35 MB, 365 signals and roughly 61,000 formatted lines;
- citation company report: 0.93 MB;
- citation Markdown report: 14 KB.

The combined bundle is an audit/interchange artifact. It is not a suitable
report prompt or direct client deliverable.

### 4.3 Confirmed quality problems to address first

The current Aprio bundle includes an unrelated running-shoes cluster and 12
zero-SOV, no-baseline Recon records produced by `news_mode`. Those triggers are
classified as gains internally even though their triage severity is `NOISE`.
The current Recon bundle exporter includes them because it filters only on
`alert_triggered`.

The integrated runner calls:

```python
start_run(..., mode="aivc", ...)
```

The supplied Agentic DB schema permits only `dry-run`, `sample`, and `live` in
`cycle_runs.mode`. The start writer catches and suppresses its insert error. If
the live constraint matches the supplied schema, the pipeline can continue
without a `cycle_runs` row and subsequently lose run history or prompt logs.

These are production blockers, not presentation preferences.

### 4.4 Existing strengths to retain

- Exact client UUID resolution in the integrated boundary.
- Immutable source-table policy.
- Checksummed, append-only root migrations.
- Strict Pydantic bundle contracts and JSON Schema validation.
- Parent pipeline/stage lifecycle in `aivc_pipeline_runs` and
  `aivc_pipeline_stages`.
- Idempotent bundle persistence.
- Recon validation and quarantine fields.
- Deterministic citation matching and attribution cautions.
- Atomic local file writing in Citation Analysis.
- Jinja2 is already a project dependency.

## 5. Architectural decision

Use six distinct layers:

```text
1. Source and producer evidence
   Citation report + citation bundle + Recon state/artifacts + Recon bundle
                              |
2. Publication gate           |
   identity, relevance, quality, materiality, quarantine
                              |
3. Decision-card composer     |
   correlate Recon and citation evidence by exact client/cluster/time
                              |
4. Report snapshot            |
   small, strict, profile-shaped, fully referenced JSON
                              |
5. Narrative adapter          |
   deterministic/reused prose first; optional editorial LLM later
                              |
6. Render and persist
   JSON + Markdown + HTML + artifact checksums + database row
```

The combined bundle remains unchanged and complete. The report snapshot is a
new derived artifact designed for humans.

## 6. Configuration design

### 6.1 Files and precedence

Add a user-editable root file:

```text
config/reporting.toml
```

Add the same safe defaults as a packaged fallback:

```text
src/aivc/resources/config/reporting.toml
```

Configuration precedence, highest first:

1. CLI `--profile`;
2. environment variable `AIVC_REPORT_PROFILE`;
3. `[report].default_profile` in the selected TOML file;
4. packaged default.

Add `AIVC_REPORT_CONFIG_PATH` to select an alternate TOML file. The default
external path is `config/reporting.toml`. If an explicitly configured file is
missing, fail. If only the default external file is missing, use the packaged
default and report that fallback in command output.

Use Python 3.12 `tomllib`; do not add another configuration dependency.

### 6.2 Initial TOML contract

```toml
config_version = "1.0"

[report]
default_profile = "decision"
write_latest_copies = true
allow_partial = false
narrative_mode = "reuse_validated"

[profiles.decision]
max_decision_cards = 5
max_recommendations = 5
max_sources_per_card = 5
max_provider_rows = 6
max_query_rows = 10
history_weeks = 8
include_query_details = false
include_full_sov_tables = false
include_evidence_appendix = false
include_data_quality_appendix = false

[profiles.detailed]
max_decision_cards = 20
max_recommendations = 15
max_sources_per_card = 20
max_provider_rows = 12
max_query_rows = 50
history_weeks = 16
include_query_details = true
include_full_sov_tables = true
include_evidence_appendix = true
include_data_quality_appendix = true
```

Validate with strict Pydantic models using `extra="forbid"`. Validate sensible
bounds for every count and duration. Unknown profiles, unknown keys, negative
limits, and unsupported configuration versions must fail before a database or
network operation.

### 6.3 Separate report depth from analysis depth

Do not map `decision` to a cheaper or weaker evidence pipeline. Do not map
`detailed` to Recon `news_mode`.

An optional, separate future analysis selector may be introduced as
`material|deep`, but it must not be controlled by the report profile. The first
implementation should retain the existing producer execution and focus on
making materiality safe.

### 6.4 Configuration provenance

Canonicalize the resolved configuration with sorted JSON and compute SHA-256.
Persist and emit:

- `config_version`;
- `report_profile`;
- `report_config_hash`;
- configuration source path or `packaged_default`;
- effective profile values.

Changing only insignificant TOML whitespace must not change the hash.

## 7. New strict report contracts

Create `src/aivc/reporting/models.py`. All models must forbid unknown fields.
Use enums/`Literal` values where appropriate and generate JSON Schemas in both:

- `schemas/final_report_snapshot.schema.json`;
- `src/aivc/resources/schemas/final_report_snapshot.schema.json`.

### 7.1 MetricDefinition

Fields:

- `metric_name`;
- `display_name`;
- `definition`;
- `unit`;
- `source_owner`;
- `methodology_version`;
- `aggregation_scope`;
- `higher_is_better` when meaningful.

Initial registered metrics:

- `literal_answer_visibility`;
- `citation_answer_coverage`;
- `owned_source_coverage`;
- `weekly_cluster_sov`;
- `sov_change_vs_baseline`;
- `provider_query_visibility`.

Never expose a generic unqualified `visibility` or `sov` number in the final
contract.

### 7.2 ReportMetric

Fields:

- metric definition key;
- value;
- numerator and denominator when applicable;
- previous value and delta when comparable;
- observation timestamp/period;
- provider/query/cluster scope;
- evidence references;
- quality flags.

### 7.3 DecisionCard

Required fields:

- stable `card_id`;
- exact `client_id`;
- `cluster_id` and label;
- optional provider/query scope;
- classification:
  `competitive_threat|visibility_opportunity|source_opportunity|defensive_gap|watch`;
- priority: `critical|high|medium|low`;
- `what_changed` as structured facts, not free-form unsupported prose;
- client metrics;
- competitor metrics;
- citation-source findings;
- page findings;
- Recon evidence summary;
- `confidence` and deterministic confidence reasons;
- recommended actions;
- measurement plan;
- alternative explanations;
- evidence references;
- warnings.

Each card must have at least one measured fact and one resolvable evidence
reference. A causal statement requires qualifying page history or independent
corroboration.

### 7.4 FinalReportSnapshot

Required top-level fields:

- schema/report version;
- report ID and idempotency key;
- parent run ID;
- client identity;
- report profile;
- resolved config metadata and hash;
- exact source bundle IDs and checksums;
- analysis/reporting period;
- report status: `complete|partial|blocked`;
- executive metrics;
- provider summary;
- topic summary;
- decision cards;
- consolidated actions;
- data-quality flags and limitations;
- methodology/metric definitions;
- evidence index;
- generated timestamp;
- snapshot checksum.

The snapshot checksum must exclude volatile timestamps and its own checksum.

### 7.5 ArtifactManifest

Record every emitted artifact:

- artifact type;
- absolute or repository-relative path;
- byte size;
- SHA-256;
- MIME type;
- generated timestamp.

## 8. Metric policy

Create `src/aivc/reporting/metrics.py` as the only final-report metric registry.
It must map producer metrics to their exact display definitions.

Rules:

1. Recon cluster SOV and citation literal visibility remain separate.
2. Percentages and percentage-point changes must not be confused.
3. Provider/query results must not be generalized to the entire monitored
   market without a supported aggregation.
4. Overall citation timelines must disclose query-cohort changes. Adding new
   queries can change a carried-forward aggregate without an actual decline on
   existing queries.
5. Report trends should prefer fixed query/provider cohorts or explicitly
   label cohort expansion.
6. A missing competitor from an answer and a measured competitor value of zero
   must remain distinguishable when the source allows it.
7. All displayed rounding happens in the renderer. Contracts retain unrounded
   numeric values.

Add methodology text for every metric to the report appendix. The decision
profile may show a short methodology but must link/reference the same contract.

## 9. Publication and relevance gate

Create `src/aivc/reporting/quality.py`. It runs before decision-card creation
and cannot be disabled by a report profile.

### 9.1 Identity validation

- Citation bundle, Recon bundle, combined bundle, report source, and parent run
  must have the same client UUID.
- Company names are display fields only after resolution.
- Add exact `--client-id` support to the CLI. `--company` remains supported but
  must fail on ambiguity.
- Validate that each Recon state/artifact belongs to the resolved client.

### 9.2 Cluster relevance validation

- Require an exact cluster ID association to the resolved client.
- Cross-check cluster label/query material against the client's configured
  cluster/topic registry where available.
- Detect clusters whose queries/topics are unsupported by onboarding/client
  configuration and flag `suspected_irrelevant_cluster`.
- Do not silently use semantic similarity to approve an otherwise unmatched
  cluster.
- A severe relevance conflict blocks publication until source data is fixed or
  an explicit, auditable client-specific allow/exclude policy is supplied.
- Fix Aprio's running-shoes source association rather than hard-coding an Aprio
  string rule in application code.

### 9.3 Recon signal gate

Exclude a Recon SOV signal from publishable decision cards when any applies:

- triage severity is `NOISE`;
- the field-resolution verdict is marked noise;
- current SOV and magnitude are both zero;
- direction is unknown and no material evidence exists;
- it is a no-baseline news-mode routine investigation mislabeled as movement;
- associated recommendation/report is quarantined;
- cluster/client identity does not match.

A real `blog_detected` item with meaningful evidence may become a
`visibility_opportunity` or `watch` card, but it must never be relabeled as a
measured SOV gain.

Update `build_recon_bundle` so audit output distinguishes:

- measured material SOV changes;
- first observations;
- informational/noise records.

The preferred initial behavior is to omit noise from the Recon signal bundle
while retaining it in Recon operational tables and decision logs.

### 9.4 Citation signal gate

- Include only material adjacent comparisons for decision cards.
- Preserve provider, query, method, and configuration comparability.
- Cap URL/source evidence according to the selected profile after ranking.
- Prioritize current coverage, coverage delta, owned/earned relationship,
  relevance, and verified page evidence.
- Do not call a URL a driver without qualifying historical page evidence.
- Treat unavailable positions, configuration mismatches, and upstream metric
  mismatches as limitations.

### 9.5 Status rules

`complete`:

- identity and relevance checks pass;
- required source artifacts exist;
- no blocking quality problem exists;
- at least one valid decision card or a valid no-material-change conclusion is
  available.

`partial`:

- the report is still truthful and useful, but optional page history or some
  non-blocking evidence is unavailable;
- every limitation is visible in the report.

`blocked`:

- wrong/ambiguous client;
- suspected severe cluster contamination;
- required Recon persistence missing;
- source bundle checksum invalid;
- no usable evidence;
- report cannot be rendered without misleading the reader.

`allow_partial` may permit `partial`, never `blocked`.

## 10. Fix Recon lifecycle compatibility before reporting

Implement and test these changes before final-report work:

1. Use `cycle_runs.mode="live"` for an integrated production run unless a new
   additive migration explicitly permits `aivc`.
2. Store `aivc` identity in the parent pipeline and requested/component metadata,
   not in a legacy constrained enum unless deliberately migrated.
3. Make failure to create the Recon `cycle_runs` row fatal.
4. Make failure to finish that row visible to the parent pipeline.
5. Include `cycle_runs`, `prompt_log`, required columns, relevant constraints,
   and required unique conflict targets in the read-only schema audit.
6. Add a test using the supplied schema constraint semantics.
7. Verify the parent stage records the exact Recon child run ID.

Do not broaden this fix into a rewrite of `sed_writer.py`.

## 11. Decision-card composition

Create:

```text
src/aivc/reporting/cards.py
src/aivc/reporting/correlation.py
src/aivc/reporting/actions.py
```

### 11.1 Correlation key

Start with exact keys only:

```text
client_id + cluster_id
```

Then narrow citation evidence by exact provider/query and competitor alias.
Do not fuzzy-match clusters during report generation.

Time alignment must be explicit. Record whether citation observations:

- precede;
- overlap;
- follow

the Recon observation window. Only overlapping or plausibly subsequent
evidence may strengthen an explanation.

### 11.2 Cross-source interpretation matrix

Implement deterministic classifications:

| Recon movement | Citation movement | Interpretation |
|---|---|---|
| material, same direction | material, same direction | corroborated movement |
| material | absent/stable | cluster movement requiring retest |
| absent/stable | material | provider/query-specific citation opportunity |
| material, opposite direction | material, opposite direction | conflicting evidence; lower confidence |
| neither material | neither material | no material change |

Page-history evidence can raise explanatory confidence only when temporal and
entity alignment passes. It must not change the measured movement itself.

### 11.3 Priority

Calculate priority deterministically from components that remain visible in
the card:

- magnitude/materiality;
- client decline or competitor gain;
- number of corroborating providers/queries;
- owned-source gap;
- readiness/content gap;
- evidence confidence;
- persistence/history;
- optional business impact if a supported value exists.

Do not use revenue estimates unless their basis is `actual`, `modeled`, or
`hybrid` and fully disclosed. Never invent a revenue impact merely to rank a
card.

### 11.4 Recommendation consolidation

Citation Analysis currently emits generic actions while Recon emits detailed
recommendations. Consolidate rather than concatenate.

Rules:

- preserve source recommendation IDs;
- deduplicate by normalized action intent, target cluster, and target asset;
- prefer a valid, specific Recon action over a generic citation action when
  both express the same intent;
- preserve citation evidence as the measurement rationale;
- cap actions by profile;
- require action, owner/category, expected time horizon, and measurement signal
  where available;
- never include quarantined actions.

## 12. Report snapshot builder

Create `src/aivc/reporting/snapshot.py`.

For a fresh integrated run, build from in-memory, run-scoped artifacts:

- resolved client identity;
- citation `CompanyIntelligenceReport` result;
- citation bundle;
- prepared Recon data and final Recon state;
- Recon bundle;
- combined bundle;
- exact parent and child run IDs.

Do not build a fresh report by querying unscoped "latest" records after the
pipeline, because another run may complete concurrently.

For a historical rerender, load by exact `parent_run_id`, verify source bundle
checksums, load the exact Recon child run artifacts, and reconstruct the same
source input. If required historical source is missing, fail or mark blocked;
do not substitute newer rows.

Profile shaping happens after the publication gate and decision-card ranking.
The detailed profile shows more valid evidence; it does not bypass filtering.

Target compact serialized snapshot sizes:

- decision profile: normally below 100 KB;
- detailed profile: normally below 500 KB;
- exceeding those values produces an observable size warning, not silent data
  loss.

## 13. Narrative policy

Initial implementation mode: `reuse_validated`.

Build narrative fields using:

- validated Recon `client_summary`, recommendation summary, probable cause,
  and evidence summary;
- deterministic citation sentences created from measured report fields;
- deterministic transition and methodology text.

Do not make another LLM call in the initial acceptance path. Recon already
performs synthesis, and a second unrestricted synthesis increases cost and
hallucination risk.

Define a `NarrativeProvider` protocol so a future `editorial_llm` implementation
can be added without changing report contracts. If later enabled, it must:

- receive only the compact snapshot, never the combined bundle;
- return a strict narrative JSON contract;
- be forbidden from adding numbers not present in the snapshot;
- pass the existing numeric/entity provenance validation;
- record model, prompt version/hash, token use, and validation result;
- fall back to deterministic prose on failure.

## 14. Rendering

Create:

```text
src/aivc/reporting/renderers/json_renderer.py
src/aivc/reporting/renderers/markdown_renderer.py
src/aivc/reporting/renderers/html_renderer.py
src/aivc/resources/templates/final_report.html.j2
src/aivc/resources/templates/final_report.md.j2
```

Use `report_jaggaer.html` as the visual/content reference. Do not copy client
data from that artifact into the template.

Renderer requirements:

- Jinja2 autoescape for HTML;
- no remote JavaScript dependency;
- no untrusted raw HTML insertion;
- print-friendly CSS;
- responsive layout;
- accessible headings, tables, chart labels, and color contrast;
- deterministic section order;
- profile-aware optional sections;
- visible report profile/status/reporting period;
- visible limitations and metric definitions;
- HTML may include only minimal local behavior such as Print/Save as PDF;
- charts must be deterministic HTML/CSS/SVG generated from snapshot data;
- no client data embedded in executable JavaScript.

Write files atomically through temporary siblings and `replace()`, matching the
existing Citation report safety pattern.

### 14.1 Output layout

Immutable run-scoped artifacts:

```text
output/<company-slug>/runs/<parent-run-id>/<profile>/final-report.json
output/<company-slug>/runs/<parent-run-id>/<profile>/final-report.md
output/<company-slug>/runs/<parent-run-id>/<profile>/final-report.html
output/<company-slug>/runs/<parent-run-id>/<profile>/artifact-manifest.json
```

When configured, atomically refresh convenient latest copies:

```text
output/<company-slug>/final-report.json
output/<company-slug>/final-report.md
output/<company-slug>/final-report.html
```

Do not use symlinks as the default on Windows.

## 15. Database migration and persistence

Add checksummed migration:

```text
migrations/0005_aivc_final_reports.sql
src/ai_visibility/resources/migrations/0005_aivc_final_reports.sql
```

Both copies must be byte-identical.

### 15.1 `aivc_final_reports`

Add an additive table with at least:

- `id uuid primary key`;
- `idempotency_key text unique not null`;
- `parent_run_id uuid not null` FK to `aivc_pipeline_runs`;
- `client_id uuid not null`;
- `report_profile text not null` checked to `decision|detailed`;
- `schema_version text not null`;
- `config_version text not null`;
- `report_config_hash text not null`;
- `input_checksum text not null`;
- `source_bundle_ids jsonb not null`;
- `source_bundle_checksums jsonb not null`;
- `status text not null` checked to `complete|partial|blocked|failed`;
- `structured_snapshot jsonb`;
- `artifact_manifest jsonb not null default '[]'`;
- `data_quality_flags jsonb not null default '[]'`;
- `generated_at timestamptz`;
- `last_error text`;
- `created_at` and `updated_at`.

Recommended idempotency components:

```text
parent_run_id + report_profile + report_config_hash + input_checksum + schema_version
```

Add indexes for client/date lookup and parent/profile lookup. Enable RLS and add
a tenant-select policy consistent with the existing AIVC tables.

Do not repurpose legacy `client_reports`, `reports`, or citation-specific
`ai_visibility_reports`. Preserve their existing meanings.

### 15.2 Repository functions

Add `src/aivc/database/final_reports.py` with:

- `persist_final_report(...)`;
- `load_final_report_by_parent(...)`;
- `load_latest_final_report(...)`;
- `mark_final_report_failed(...)`;
- exact transaction boundaries and idempotent upsert;
- checksum verification before persistence;
- no database storage of secrets;
- no mutation of source or legacy history.

Persist the snapshot before refreshing latest local copies. A failed local
write must produce a failed/partial stage with a clear error; it must not leave
a completed report row pointing to nonexistent artifacts.

## 16. Orchestration changes

Create `src/aivc/orchestration/report_pipeline.py` and keep the existing
producer orchestration focused.

Add parent stages:

1. `report_preflight`;
2. `publication_gate`;
3. `decision_cards`;
4. `report_snapshot`;
5. `report_render`;
6. `report_persist`.

For a fresh command:

```text
resolve exact client
-> run existing integrated Citation + Recon pipeline
-> verify citation/recon/combined bundles
-> publication gate
-> build cards and snapshot
-> validate schema/checksum
-> render run-scoped artifacts
-> persist final report and manifest
-> refresh latest copies
-> optional delivery after all required persistence
```

If the current integrated pipeline creates and finishes its parent before the
report stages, refactor it so the final report can be part of the same parent
lifecycle without marking the parent complete prematurely. Prefer an internal
orchestration service returning run-scoped producer results rather than
nesting one completed parent inside another.

Final parent status rules:

- `completed` only when required producer and final-report stages succeed and
  the report is complete;
- `partial` when the allowed report is partial;
- `failed` when generation fails;
- a blocked report should leave an explicit blocked report row/stage and map
  the parent to `partial` or `failed` according to the existing parent enum;
  record `blocked` in the report itself.

## 17. Page enrichment behavior

Page enrichment remains optional evidence, not a hard dependency.

Initial command behavior:

- use page snapshots already available at report-build time;
- queue material pages through existing citation generation;
- disclose missing historical page evidence;
- do not require an operator to rerun merely to receive a truthful base report.

Add an optional later flag:

```powershell
--wait-for-pages
```

If implemented in this phase, it must:

- process only bounded, prioritized jobs for the resolved client;
- enforce a total timeout and job limit;
- preserve SSRF/robots/byte protections;
- rebuild citation evidence and the final report after processing;
- degrade to partial on non-blocking page failures;
- never hold a database transaction while fetching the web.

It is acceptable to defer `--wait-for-pages` until after the core report
acceptance criteria pass.

## 18. CLI design

Add a `report` Typer group to `aivc`.

### 18.1 Fresh report

```powershell
uv run aivc report generate --company "Aprio" --profile decision
```

Support exact UUID:

```powershell
uv run aivc report generate --client-id "<uuid>" --profile detailed
```

`--company` and `--client-id` are mutually exclusive; one is required.

Options:

- `--profile decision|detailed`;
- `--config <path>`;
- `--allow-partial` as an explicit run override;
- later `--wait-for-pages` and bounded page options.

### 18.2 Historical rerender

```powershell
uv run aivc report render --parent-run-id "<uuid>" --profile detailed
```

This must never rerun producers. It loads exact persisted sources and fails if
they cannot reproduce a truthful snapshot.

### 18.3 Inspection

```powershell
uv run aivc report show --parent-run-id "<uuid>"
uv run aivc report validate --path "output/.../final-report.json"
```

### 18.4 Command output

Print concise JSON containing:

- status;
- client ID/name;
- parent run ID;
- report ID;
- profile/config hash;
- source bundle IDs/checksums;
- card/action counts;
- quality flags;
- artifact paths/checksums;
- whether latest copies were refreshed.

Never print secrets or the full report payload to stdout by default.

Keep existing `aivc run` and `aivc citations generate` behavior compatible.
After the new report command is stable, `aivc run` may gain an explicit
`--generate-report` option; do not silently change its cost or outputs.

## 19. File-level implementation map

### Add

```text
config/reporting.toml
schemas/final_report_snapshot.schema.json
migrations/0005_aivc_final_reports.sql
src/ai_visibility/resources/migrations/0005_aivc_final_reports.sql
src/aivc/resources/config/__init__.py
src/aivc/resources/config/reporting.toml
src/aivc/resources/templates/final_report.html.j2
src/aivc/resources/templates/final_report.md.j2
src/aivc/reporting/__init__.py
src/aivc/reporting/config.py
src/aivc/reporting/models.py
src/aivc/reporting/metrics.py
src/aivc/reporting/quality.py
src/aivc/reporting/correlation.py
src/aivc/reporting/cards.py
src/aivc/reporting/actions.py
src/aivc/reporting/snapshot.py
src/aivc/reporting/narrative.py
src/aivc/reporting/artifacts.py
src/aivc/reporting/renderers/__init__.py
src/aivc/reporting/renderers/json_renderer.py
src/aivc/reporting/renderers/markdown_renderer.py
src/aivc/reporting/renderers/html_renderer.py
src/aivc/database/final_reports.py
src/aivc/orchestration/report_pipeline.py
tests/unit/test_report_config.py
tests/unit/test_report_quality.py
tests/unit/test_report_metrics.py
tests/unit/test_decision_cards.py
tests/unit/test_report_snapshot.py
tests/unit/test_final_report_renderers.py
tests/unit/test_final_report_persistence.py
tests/unit/test_final_report_cli.py
tests/integration/test_final_report_pipeline.py
```

### Modify

```text
.env.example
README.md
docs/OPERATIONS_GUIDE.md
pyproject.toml package resource configuration if required
src/aivc/cli/app.py
src/aivc/config/settings.py
src/aivc/contracts/__init__.py as appropriate
src/aivc/database/__init__.py
src/aivc/database/schema_audit.py
src/aivc/orchestration/__init__.py
src/aivc/orchestration/pipeline.py
src/aivc/producers/recon_bundle.py
src/ai_visibility/companies/resolver.py
src/scout/runner.py
src/scout/db/sed_writer.py
```

Do not edit the imported `Scout-Agent-AIVC` reference copy.

## 20. Tests

### 20.1 Configuration

- packaged defaults load from an installed wheel;
- root TOML loads in the repository;
- CLI overrides environment, environment overrides TOML;
- decision/detailed profiles resolve correctly;
- invalid keys and bounds fail;
- whitespace-only TOML changes do not change effective config hash;
- effective config is deterministic.

### 20.2 Lifecycle regression

- integrated Recon uses a DB-compatible run mode;
- start-run failure fails the Recon stage;
- prompt logging has a valid run parent;
- finish-run failures are surfaced;
- schema audit identifies missing constraints/conflict targets where practical.

### 20.3 Quality gate

- zero-SOV news-mode gains are excluded;
- `NOISE` verdicts are excluded;
- valid first observations are labeled correctly rather than gains;
- quarantined recommendation/report pairs are excluded;
- mismatched client IDs block;
- checksum mismatch blocks;
- severe irrelevant-cluster suspicion blocks;
- a blog-only evidence item is never called an SOV gain;
- partial page evidence produces partial, not false completeness.

Include an Aprio-like fixture with a running-shoes cluster to prove the report
cannot publish it silently.

### 20.4 Metrics

- percentages and percentage points render correctly;
- fixed-cohort trend behavior is tested;
- provider/query isolation is retained;
- SOV and literal visibility remain distinct;
- numerator/denominator provenance survives projection;
- rounding happens only in rendering.

### 20.5 Correlation/cards

- exact client/cluster correlation;
- alias matching is exact/boundary-aware and client-scoped;
- temporal alignment behavior;
- corroborated, unsupported, provider-specific, and conflicting cases;
- evidence refs all resolve;
- no card without measured facts;
- recommendation deduplication and source preservation;
- profile caps are applied after quality/ranking.

### 20.6 Rendering and security

- strict schema validation before rendering;
- HTML escaping for client/query/source text;
- no raw script injection;
- deterministic output for fixed inputs excluding allowed timestamps;
- decision and detailed golden/snapshot tests;
- optional sections appear only in detailed;
- HTML headings and print control match accessibility expectations;
- atomic writes leave no partial final artifact;
- artifact hashes/sizes match actual files;
- installed wheel contains schemas, config, and templates.

### 20.7 Persistence

- idempotent rerun updates rather than duplicates;
- different profile/config/input produces a different idempotency key;
- exact parent/client relationship;
- failed render does not create a misleading complete row;
- RLS migration shape matches existing AIVC policy style;
- no query mutates `public.ai_monitoring`.

### 20.8 End-to-end fixture

Build a deterministic fixture with:

- one client;
- two relevant clusters;
- one contaminated cluster;
- material and non-material citation changes;
- material Recon movement;
- one NOISE movement;
- one quarantined recommendation;
- optional page evidence;
- overlapping generic/specific actions.

Assert that:

- decision report contains only the top valid cards;
- detailed report contains more valid detail but no blocked/noise content;
- both reference the same source bundles and use the same metrics;
- both JSON/Markdown/HTML outputs validate;
- the combined bundle remains complete and unmodified.

## 21. Migration and live-safety procedure

Before applying `0005`:

1. Run the read-only schema audit.
2. Inspect the live `cycle_runs.mode` constraint and required unique indexes.
3. Compare live columns to `agentic_db.sql`, recognizing that the supplied file
   is a context snapshot rather than executable migration history.
4. Confirm `DATABASE_URL` and `SUPABASE_URL` resolve to the same intended
   Agentic project.
5. Record `public.ai_monitoring` row count and maximum `created_at`.

Then:

```powershell
uv run ai-visibility db migrate
```

After migration, verify:

- only the new AIVC report table/index/policy objects were added;
- no Recon table was emptied or rewritten;
- `public.ai_monitoring` count and maximum timestamp are unchanged by the
  migration;
- rerunning migrations applies nothing and reports no checksum drift.

Do not apply legacy Recon SQL to repair a missing constraint. Write a new,
reviewed, additive root migration if a live compatibility change is needed.

## 22. Implementation phases and stopping gates

### Phase 0: Baseline

- Preserve all user changes/reference files.
- Run Ruff, mypy, pytest, and build.
- Record current output/bundle behavior.

Gate: baseline understood; unrelated failures documented before edits.

### Phase 1: Recon correctness blockers

- Fix cycle run mode/error propagation.
- Expand schema audit.
- Exclude or correctly classify zero/noise signals in the Recon bundle.
- Add exact client-ID resolver/CLI plumbing.

Gate: no integrated run can appear complete without its required Recon run and
persistence; Aprio-like zero/noise items do not become publishable SOV gains.

### Phase 2: Config and contracts

- Add TOML config loading, strict models, precedence, and hash.
- Add report models and schemas.
- Add metric registry.

Gate: both profiles resolve deterministically and schemas package correctly.

### Phase 3: Quality, correlation, and cards

- Add publication/relevance gate.
- Add cross-source correlation and confidence.
- Add recommendation consolidation.
- Add decision/detailed shaping.

Gate: deterministic fixture cards are truthful, referenced, ranked, and free
of noise/contamination.

### Phase 4: Snapshot, rendering, artifacts

- Build run-scoped snapshot.
- Reuse validated narrative.
- Create JSON/Markdown/HTML renderers and template.
- Add atomic artifacts and manifest.

Gate: both profile outputs pass schema, security, snapshot, and packaging tests.

### Phase 5: Persistence and orchestration

- Add/apply `0005`.
- Add final report repository.
- Add report parent stages and correct final status behavior.
- Add fresh and historical report services.

Gate: idempotent persisted reports and exact-run rerendering work.

### Phase 6: CLI and documentation

- Add `aivc report generate/render/show/validate`.
- Update `.env.example`, README, and operations guide.
- Document the single config switch and CLI override.

Gate: an operator can create either report without editing code or manually
running SQL.

### Phase 7: Live verification

- Apply migration safely.
- Run exact-client preflight and fix upstream Aprio contamination before
  client-facing acceptance.
- Generate decision and detailed Aprio reports.
- Verify persisted artifacts, source checksums, and immutable source count.

Gate: acceptance criteria below all pass or the live run reports a truthful,
specific credential/data blocker.

## 23. Quality commands

Run after each material phase and at completion:

```powershell
uv run ruff check .
uv run mypy
uv run pytest -q
uv build
```

Add focused commands while developing:

```powershell
uv run pytest -q tests/unit/test_report_config.py
uv run pytest -q tests/unit/test_report_quality.py
uv run pytest -q tests/unit/test_decision_cards.py
uv run pytest -q tests/integration/test_final_report_pipeline.py
```

Do not lower coverage thresholds, loosen strict mypy, or add broad ignores to
make the implementation pass.

## 24. Final acceptance criteria

### Functional

- One TOML value changes the default report between `decision` and `detailed`.
- `--profile` overrides it for one command.
- Both profiles use identical validated source analysis.
- Decision output is concise and detailed output adds only valid detail.
- Full producer/combined bundles remain unchanged and persisted.
- Final JSON, Markdown, and HTML are generated atomically.
- Reports are persisted idempotently and linked to one exact parent run.
- Historical rerender never mixes in newer data.

### Truthfulness

- No NOISE or zero-SOV gain appears as a material client finding.
- No suspected irrelevant cluster is silently published.
- No quarantined recommendation is published.
- Citation and SOV metrics are clearly distinct.
- Every displayed number traces to a report metric/evidence reference.
- Causal language follows page-history rules.
- Missing evidence is disclosed.

### Safety

- `public.ai_monitoring` remains unchanged by migrations, analysis, reporting,
  and rendering.
- Only derived Recon, `ai_visibility_*`, and `aivc_*` tables are written by
  their owning stages.
- HTML escapes untrusted content.
- Secrets never appear in reports, stdout, logs, or artifact manifests.

### Operator experience

These commands work from the repository root:

```powershell
uv run aivc report generate --company "Aprio" --profile decision
uv run aivc report generate --company "Aprio" --profile detailed
```

The recommended production form uses UUID:

```powershell
uv run aivc report generate --client-id "<aprio-client-uuid>" --profile decision
```

The CLI prints concise artifact paths, IDs, checksums, counts, and limitations.
No manual SQL or LLM copy/paste step remains.

## 25. Expected final outputs

```text
output/aprio/
  company-intelligence-report.json
  company-intelligence-report.md
  citation-signal-bundle.json
  recon-signal-bundle.json
  combined-signal-bundle.json
  final-report.json
  final-report.md
  final-report.html
  runs/
    <parent-run-id>/
      decision/
        final-report.json
        final-report.md
        final-report.html
        artifact-manifest.json
      detailed/
        ...
```

The current citation report and bundles remain producer artifacts. The new
`final-report.*` files are the client-facing, unified deliverable.

## 26. Implementation handoff instruction

Implement this plan phase by phase. Do not stop after scaffolding, contracts,
or rendering. Correct the Phase 1 lifecycle/publication blockers first. Keep
the worktree's user-provided reference files intact. Test each phase, apply only
safe additive migrations, and continue until the decision and detailed report
commands pass the functional, truthfulness, safety, and quality acceptance
criteria, unless blocked by missing live credentials or confirmed upstream
Aprio data contamination that requires an external data-owner correction.
