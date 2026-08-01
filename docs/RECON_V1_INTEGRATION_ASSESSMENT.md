# Recon V1 + AI Citation Analysis Integration Assessment

Status: draft architecture, pending the supplied system architecture diagram.

## Scope

This assessment covers integration between:

- Recon V1 at `C:\Users\harso\Desktop\AIVC\Scout-Agent-AIVC`
- the AI citation analysis project in this repository

GA4 and GSC are explicitly out of scope. The integration must not query their
tables, load their modules, add their fields to the shared contract, or depend
on their credentials.

No Recon V1 files or databases were changed during this assessment.

## Recommendation

Do not copy Recon V1 into this package or join the two pipelines inside one
Python process yet. Keep them as independent signal producers and integrate
them through a strict, versioned JSON contract.

The target flow is:

```text
public.ai_monitoring (read-only)             Recon source systems
                |                                      |
                v                                      v
      AI citation producer                    Recon V1 producer
                |                                      |
                +-------- SignalBundle v1 JSON --------+
                                      |
                                      v
                           bundle validator/merger
                                      |
                                      v
                         future report composition
```

This boundary is preferable because the projects currently differ in their:

- database clients and credentials;
- runtime configuration and `.env` conventions;
- Python/dependency management;
- persistence models;
- confidence and signal schemas;
- use of deterministic logic versus LLM synthesis.

JSON also provides a replayable audit artifact and prevents either producer
from writing into the other producer's tables.

## What each project should own

### AI citation analysis producer

This project remains the canonical owner of:

- normalization of `public.ai_monitoring`;
- literal company mention measurement;
- provider/query timelines;
- citation URL occurrence and answer-coverage measurement;
- page snapshots and page diffs;
- attribution scores, alternatives, warnings, and data-quality flags;
- deterministic AI-visibility signals.

It must remain the only integration component that reads
`public.ai_monitoring`. That source remains immutable and read-only.

### Recon V1 producer

Recon remains the canonical owner of:

- SOV movement detection and triage severity;
- website-change investigation;
- external web/SERP intelligence;
- blog detections;
- client-site readiness;
- historical cluster context;
- Recon recommendations and validation/quarantine status.

Recon's current `ai_response_analysis` output overlaps the citation producer.
It should not be treated as a second canonical calculation. During the first
integration phase it can be exported as legacy Recon evidence. In the final
integration it should consume an adapted citation signal or be disabled when a
validated citation bundle is supplied.

## Required shared contract

Use one top-level bundle per producer, client, and producer run.

```json
{
  "schema_version": "1.0.0",
  "bundle_id": "uuid",
  "producer": {
    "name": "ai_visibility|recon_v1",
    "version": "string",
    "run_id": "string"
  },
  "client": {
    "client_id": "uuid",
    "canonical_name": "string",
    "official_domains": ["example.com"]
  },
  "period": {
    "start": "date-time|null",
    "end": "date-time|null"
  },
  "generated_at": "date-time",
  "status": "complete|partial|failed",
  "signals": [],
  "recommendations": [],
  "evidence": {},
  "data_quality": {
    "flags": [],
    "warnings": [],
    "abstentions": []
  }
}
```

Every signal should have the same portable fields:

```json
{
  "signal_id": "stable hash or uuid",
  "signal_type": "namespaced string",
  "subject": {
    "client_id": "uuid",
    "company": "string",
    "competitor": "string|null"
  },
  "scope": {
    "cluster_id": "string|null",
    "cluster_label": "string|null",
    "monitor_query_key": "string|null",
    "query": "string|null",
    "provider": "string|null"
  },
  "observed_at": "date-time|null",
  "direction": "increase|decrease|new|removed|stable|unknown",
  "magnitude": {
    "previous": "number|null",
    "current": "number|null",
    "delta": "number|null",
    "unit": "ratio|percentage_point|count|score|none"
  },
  "confidence": "low|medium|high|unknown",
  "evidence_refs": ["evidence-id"],
  "hypotheses": [],
  "recommended_actions": [],
  "warnings": [],
  "source_payload": {}
}
```

Rules for this contract:

1. `client_id` is mandatory and is the primary join key. Company names are
   display data, not identity keys.
2. `cluster_id`, query key, provider, and time bounds are carried whenever the
   producer has them. They must never be reconstructed from prose.
3. Measurements always carry a unit. Ratios from the citation producer must
   not be silently compared with Recon percentage points.
4. Measured observations, hypotheses, and recommendations are distinct fields.
5. Evidence is content-addressed or otherwise stable, and signals reference it
   rather than duplicating large evidence objects.
6. Unknown and unavailable values remain `null`/`unknown`; they are not changed
   to zero.
7. Each bundle is validated against JSON Schema before it can be merged.
8. The merger rejects client-ID mismatches and incompatible time periods. It
   does not guess.
9. Producer-specific fields can survive under `source_payload`, but consumers
   cannot depend on them without a versioned contract change.
10. GA4/GSC fields are prohibited in contract v1.

## Initial field mapping

### AI citation report to SignalBundle

| Current field | Shared field |
|---|---|
| `company.client_id` | `client.client_id` |
| `company.canonical_name` | `client.canonical_name` |
| `company.official_domains` | `client.official_domains` |
| `analysis_period.start/end` | `period.start/end` |
| `signals[].signal_type` | `signals[].signal_type` with `ai_visibility.` prefix |
| `signals[].observed.company` | `signals[].subject.company` |
| `signals[].observed.provider/query` | `signals[].scope.provider/query` |
| visibility previous/current/delta | `signals[].magnitude`, unit `ratio` |
| `signals[].primary_hypothesis` | `signals[].hypotheses[0]` |
| `signals[].evidence` | bundle `evidence` plus `evidence_refs` |
| warnings and report flags | signal warnings and bundle data quality |

Before exporting, citation signals need to add fields they can already derive
but do not currently retain in the signal object: `monitor_query_key`,
`cluster_id`, `cluster_label`, `previous_at`, and `current_at`.

### Recon V1 to SignalBundle

| Recon field/table | Shared field |
|---|---|
| run ID and sync date | producer run ID and bundle period |
| `scout_decision_log` / `ClusterVerdict` | triage signals |
| `sov_tracking` | SOV evidence and magnitude in percentage points |
| `investigation_triggers` | trigger signals |
| `investigations.website_changes` | evidence object |
| `investigations.third_party_signals` | evidence object |
| `investigations.ai_citation_changes` | legacy evidence only |
| `blog_detections` | blog signals/evidence |
| `client_readiness` | readiness signals/evidence when persisted/exportable |
| `recommendations` | shared recommendations |
| `validation_status` and notes | eligibility, warnings, and data quality |

Only artifacts belonging to the exact `(client_id, run_id)` may be exported.
Quarantined recommendations remain available for internal audit but must be
marked ineligible for a client-facing report.

## Recon V1 blockers to fix before integration

The following are correctness or isolation defects in the inspected code, not
style preferences.

### P0: client scope is lost in generated artifacts

`Recommendation`, `InternalReport`, and `ClientSummary` do not retain
`client_id`; reports also do not retain all cluster identity. Downstream code
therefore reconstructs identity from client name or list position.

Required fix: carry `client_id` and cluster identity on every artifact from
creation through persistence and export.

### P0: cross-client SOV lookup

Both recommendation and report generation index client SOV using only
`cluster_id`. If two clients share a cluster ID, the first client's SOV can be
used in the second client's report.

Required fix: key by `(client_id, cluster_id)` everywhere.

### P0: cross-client persistence ID collisions

Investigation and recommendation ID maps use `(competitor_name, cluster_id)`
without `client_id`. Reports and outcomes consume those maps. Two clients with
the same competitor and cluster can receive the wrong foreign key.

Required fix: key every map by
`(client_id, competitor_name, cluster_id)` and test the complete write chain.

### P0: AI response bundle is not client-scoped

Recon's `get_ai_response_bundle` filters `ai_responses` by `cluster_id` but not
`client_id`. Shared cluster IDs can mix one client's AI responses into another
client's investigation.

Required fix: add `client_id` to the API and query, or replace this branch with
the canonical citation bundle adapter.

### P0: exporter broadens failed filters

The current `pull` helper retries a failed `(client_id, run_id)` query with only
one filter. A schema error or transient failure can therefore return other runs
or another client's rows.

Required fix: fail closed. Never loosen tenant/run scope. Validate every
returned row against the requested identity before writing output.

### P0: existing exporter touches GA4/GSC unconditionally

`export_client_package` reads both analytics sources regardless of the `scope`
argument. The `scope` option does not prevent those reads.

Required fix for this project: do not reuse that exporter. Build a new
`signals export` path whose query allow-list contains no GA4/GSC tables or
modules.

### P0: persistence failures are reported as success

Recon's persistence dispatcher catches individual write failures and returns
zero counts, while `run.py` still marks the lifecycle row completed. This can
produce a successful-looking partially persisted run.

Required fix: return structured `counts` and `failures`, then mark the run
failed or partial according to an explicit required-artifact policy.

### P1: client resolution is ambiguous

The Recon exporter accepts the first partial client-name match. This is unsafe
for similarly named clients.

Required fix: exact UUID is preferred; a name must resolve to exactly one
client or fail with a list of candidates.

### P1: intended regression tests are ignored

Recon's `.gitignore` ignores the entire `tests/` directory. The local tests
describe fixes for client scoping and persistence failure reporting, but the
inspected implementation does not contain those fixes. As a result the tests
are neither version-controlled nor an enforceable CI gate.

Required fix: track tests, add test dependencies/configuration, and make the
suite part of CI.

### P1: database schema reference is stale

`scout/db/schema.sql` describes legacy table names while the live writer uses a
different table set and relies on piecemeal migrations.

Required fix: establish one ordered migration history and generate/reference a
current schema snapshot from it.

### P1: Recon virtual environment is not portable

The checked local environment points to a Python executable that is no longer
available. The project has no lock file and no declared development/test
dependency group.

Required fix: standardize on Python 3.12, add a lock file, and recreate the
environment rather than copying `.venv` between machines.

## Merge phases

### Phase 0: freeze the boundary and repair Recon correctness

- Do not move code between repositories.
- Fix the P0 identity, exporter, and persistence defects above.
- Track and run Recon tests.
- Add explicit feature configuration that keeps all GA4/GSC reads disabled.
- Establish one exact `client_id` for the integration fixture.

Exit criteria: both producers can run independently without cross-client or
partial-success ambiguity.

### Phase 1: create the contract package

- Add `schemas/signal_bundle.schema.json`.
- Add strict Pydantic contract models in a small dependency-light package.
- Add canonical hashing and schema-version compatibility rules.
- Add fixtures for citation-only, Recon-only, combined, partial, mismatched
  client, mismatched units, and quarantined outputs.

Exit criteria: both repositories validate the same fixtures byte-for-byte.

### Phase 2: add the AI citation exporter

- Convert the existing report model to `SignalBundle` without recomputing
  metrics.
- Preserve query key, cluster, provider, comparison times, evidence references,
  confidence, and warnings.
- Add a CLI command that writes JSON atomically.
- Keep `public.ai_monitoring` read-only and keep all writes namespaced to the
  existing derived tables/output directory.

Exit criteria: an Aprio citation bundle validates and is reproducible.

### Phase 3: add a clean Recon exporter

- Query only an explicit allow-list of Recon-owned core tables.
- Require exact `(client_id, run_id)` on every row.
- Do not import or query GA4/GSC code.
- Preserve measured data separately from LLM hypotheses.
- Export quarantined artifacts as internal-only/ineligible.

Exit criteria: Recon emits a valid bundle with zero analytics-table calls.

### Phase 4: merge bundles

- Validate each input independently.
- Require equal `client_id`.
- Check time-period compatibility.
- Namespace signal types and deduplicate only by declared stable keys.
- Preserve all producer provenance.
- Emit a combined bundle; do not generate the final prose report yet.

Exit criteria: combined JSON is deterministic, replayable, and retains both
producers' evidence without double-counting AI citation signals.

### Phase 5: orchestration and future reporting

- Add a single command that runs or accepts outputs from both producers.
- Record producer failures independently so one partial source does not masquerade
  as a complete report.
- Feed the validated combined bundle to the later report composer.
- Keep report rendering separate from signal calculation.

This phase should be finalized only after the architecture diagram is reviewed.

## Required tests

At minimum, the integration needs:

- two clients sharing a cluster ID;
- two clients sharing the same competitor and cluster;
- ambiguous company names;
- exact run scoping in every Recon export query;
- a forced database write failure producing non-complete run status;
- a test proving no GA4/GSC table or module is touched;
- ratio versus percentage-point unit preservation;
- citation signal export retaining query/cluster/time identity;
- bundle client-ID mismatch rejection;
- schema-version mismatch rejection;
- deterministic IDs and byte-stable re-export;
- quarantined recommendation exclusion from client-eligible output;
- citation overlap deduplication without discarding Recon evidence;
- partial-producer behavior;
- JSON Schema and Pydantic round trips.

## Decisions to confirm from the architecture diagram

The diagram should settle these remaining choices:

1. Whether the two producers run on the same host or as separate jobs.
2. Whether bundles are exchanged through files, object storage, or a dedicated
   integration table.
3. Which system owns orchestration and final report composition.
4. Whether Recon must consume citation signals during recommendation generation
   or only contribute independent output to the later composer.
5. The authoritative mapping between Recon client IDs and the
   `public.ai_monitoring.client_id` values.

