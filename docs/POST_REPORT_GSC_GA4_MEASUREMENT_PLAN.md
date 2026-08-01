# Post-Report GSC and GA4 Growth Measurement Plan

Status: deferred implementation specification

Implementation timing: begin only after the Recon V1 integration and shared
signal contract are complete.

Source reference: `gsc-ga4-supabase.pdf`, reviewed 2026-08-01.

## 1. Purpose

The AI Visibility report explains what changed in monitored AI answers,
mentions, citations, providers, queries, competitors, and cited pages. It does
not currently answer whether those changes were followed by growth in Google
Search or on the client's website.

This future module will connect three layers of evidence:

```text
AI visibility evidence
        |
        v
GSC search evidence
        |
        v
GA4 website and conversion evidence
```

The result will be a post-report Growth Impact Report that answers:

- Did visibility for the monitored topic improve or decline?
- Did relevant Google queries/pages gain impressions or clicks afterward?
- Did relevant landing pages gain sessions or engagement afterward?
- Were more conversions or revenue recorded, when those metrics are available?
- Is the result measurable, mixed, too early, or inconclusive?

The module reports temporal association. It must not claim that an AI
visibility change caused search, traffic, conversion, or revenue growth unless
a stronger experimental design is added later.

## 2. Position in the overall system

This is a downstream measurement module, not part of core AI visibility
calculation and not part of Recon V1.

```text
Recon V1 bundle ----+
                    |
                    +--> unified signal bundle --> final intelligence report
                    |                                  |
Citation bundle ----+                                  v
                                               measurement plan
                                                        |
                                  existing GSC/GA4 system data
                                                        |
                                                        v
                                             Growth Impact Report
```

The core report must still succeed when GSC, GA4, or this measurement module is
unavailable. Measurement is asynchronous because complete Google data has an
intentional two-day delay and meaningful outcome windows take several weeks.

## 3. Existing GSC/GA4 capabilities to reuse

The supplied handover document shows that the existing product already owns:

- per-organization GSC and GA4 OAuth connections;
- OAuth callbacks and property selection;
- credential persistence in `public.user_metrics`;
- GSC collection into `public.gsc_query_page_metrics`;
- GA4 collection into `public.ga4_metrics`;
- a daily `POST /revenue/cron/sync` refresh endpoint;
- GSC query-to-page and GA4 landing-page normalization;
- an existing query-to-revenue join based on normalized host and path;
- classification of AI-referral, organic, and unattributed GA4 traffic;
- identification of AI-referral pages that have no corresponding GSC query;
- in-process TTL caching and date-window normalization.

Therefore this project must not create another OAuth flow, another Google API
client, or another credential store. It should consume safe, client-scoped
metrics exposed by the existing backend.

## 4. Scope

### In scope

- Create a measurement plan from a completed intelligence report.
- Map report signals to measurable queries and client-owned landing pages.
- Read client-scoped, aggregated GSC and GA4 metrics from the existing system.
- Capture equal baseline and follow-up windows.
- Calculate deterministic metric deltas.
- Generate JSON and Markdown Growth Impact Reports.
- Persist only measurement plans, aggregate snapshots, and outcomes in new
  `ai_visibility_*` tables.
- Preserve source freshness, mapping method, confidence, and warnings.
- Support GSC-only, GA4-only, combined, waiting, partial, and inconclusive
  results.

### Out of scope

- Building or changing the browser OAuth experience.
- Reading, copying, returning, logging, or rotating Google refresh tokens.
- Replacing the existing GSC/GA4 collection services.
- Automatically changing client websites or content.
- Automatically publishing recommendations.
- Treating a report-generation timestamp as proof of an implemented action.
- CRM ingestion or pushing purchase events into GA4.
- Reimplementing the existing query-revenue algorithm.
- Claiming causal business impact from a before/after comparison alone.
- Building a frontend in this backend-only repository.

## 5. Core design principles

### 5.1 Existing analytics backend owns Google access

Only the existing FastAPI services should access Google APIs or OAuth
credentials. This project consumes a safe measurement response containing
metrics, identity, scope, freshness, and row counts.

### 5.2 Identity is always client-scoped

Every request and response must contain an exact `client_id`. Names, domains,
GSC site URLs, and GA4 property IDs are not substitutes for client identity.

### 5.3 Fail closed on tenant ambiguity

The handover notes that the raw GSC table is keyed by `site_url`, while the GA4
table is keyed by `property_id`, not `client_id`. Multiple organizations may
share either property. The measurement interface must apply client scope before
returning data and must reject requests when safe domain scoping is unavailable.

### 5.4 Do not expose credentials

The current `user_metrics` table also holds refresh tokens. This project must
never issue `select *` against it and should ideally never query the table at
all. A safe API response or a deliberately restricted database view is
required.

### 5.5 Measurements and interpretations are separate

Raw aggregate values and arithmetic deltas are deterministic. Labels such as
"associated growth" are interpretations. Both must remain visible and
separate.

### 5.6 Missing is not zero

Unavailable metrics, unmapped pages, disconnected integrations, incomplete
windows, and failed syncs remain `null` or explicit status values. They must not
be converted to zero.

### 5.7 Units are explicit

Counts, currency, ratios, percentages, percentage points, and average position
are different units. Every metric definition and delta must retain its unit.

## 6. Recommended integration boundary

### 6.1 Preferred option: a safe internal measurement API

Add a client-scoped endpoint to the existing FastAPI analytics backend. The
exact route name can follow that repository's conventions; this document uses:

```text
POST /internal/measurement/snapshot
```

Example request:

```json
{
  "client_id": "uuid",
  "start_date": "2026-08-01",
  "end_date": "2026-08-28",
  "queries": ["government contract accounting"],
  "pages": ["https://www.aprio.com/example-page"],
  "include": ["gsc", "ga4"]
}
```

Example response shape:

```json
{
  "schema_version": "1.0.0",
  "client_id": "uuid",
  "requested_window": {
    "start": "2026-08-01",
    "end": "2026-08-28"
  },
  "effective_window": {
    "start": "2026-08-01",
    "end": "2026-08-28"
  },
  "fresh_through": "2026-08-28",
  "scope": {
    "queries": [],
    "pages": [],
    "query_mapping": [],
    "page_mapping": []
  },
  "gsc": {
    "status": "available",
    "row_count": 0,
    "metrics": {}
  },
  "ga4": {
    "status": "available",
    "row_count": 0,
    "metrics": {}
  },
  "warnings": []
}
```

The endpoint must:

- authenticate the service caller;
- authorize the exact client;
- resolve the client's site/property internally;
- reuse the existing normalization and domain-scoping logic;
- return aggregate data only;
- never return refresh tokens or unrestricted property data;
- include the effective date range and source freshness;
- reject unsafe shared-property requests rather than fail open;
- return explicit source statuses and warnings.

This is preferable to direct table access because the existing backend already
owns tenant resolution, page normalization, shared-property filtering, and
metric semantics.

### 6.2 Fallback option: restricted read-only database views

If an internal API cannot be added, create views that expose only safe metric
columns plus an explicit `client_id`. The database role used by this project
must have `SELECT` on those views and no access to `user_metrics` or raw secret
columns.

This option requires careful handling of shared GSC/GA4 properties and domain
filtering. It must not be implemented as a broad join to `user_metrics` exposed
through the current service-role key.

## 7. Prerequisite changes to the intelligence report contract

The current citation report has most of the evidence needed, but each exported
signal must retain the following fields before measurement integration begins:

- stable `signal_id`;
- exact `client_id`;
- `monitor_query_key`;
- original monitored query;
- `cluster_id` and `cluster_label`, when present;
- provider;
- previous and current run IDs;
- previous and current observation timestamps;
- signal direction and magnitude;
- client-owned target URLs, if known;
- cited third-party URLs kept separately from client-owned target URLs;
- confidence and warnings;
- report ID and report generation time.

The unified Recon/citation bundle should provide the same identity and scope
fields. Measurement must never reconstruct them from generated prose.

## 8. Measurement plan

One or more plans are created after a completed report is persisted. A plan is
not an outcome; it describes what can be measured later.

### 8.1 Plan inputs

- report ID;
- source signal ID;
- client ID;
- monitored query and/or cluster;
- client-owned target pages;
- signal observation time;
- optional action-completion time supplied by another system later;
- baseline and follow-up window definitions;
- expected available sources: GSC, GA4, or both.

### 8.2 Anchor time

Use the strongest available anchor:

1. `action_completed_at`, if an external execution system provides it;
2. otherwise `report_generated_at`.

When the report time is used, the result must be labeled
`observation_only`. It means growth occurred after the report, not after a
confirmed implementation.

### 8.3 Default windows

Use equal 28-day windows to avoid weekday imbalance:

```text
Baseline: 28 complete days immediately before the anchor
Follow-up: 28 complete days immediately after the anchor
```

The follow-up is eligible for final evaluation only after its last day is at
least two days old because the existing Google integration clamps data to
`today - 2`.

Allow alternative 7-, 14-, and 56-day windows through configuration, but use
28 days as the default. Store the chosen policy version on every plan.

### 8.4 Target selection

Targets must be selected in this order:

1. Explicit client-owned target URL supplied by an approved action or report.
2. Client-owned page already present in report evidence.
3. A validated cluster-to-client-page mapping from the upstream product.
4. Query-only GSC measurement when no page can be identified.
5. Unmapped, when none of the above is safe.

Third-party citation URLs must never be queried as if they were the client's
GA4 landing pages.

## 9. Matching logic

### 9.1 Page normalization

Reuse the existing GSC/GA4 host-and-path normalization:

- lowercase hostname;
- remove `www.` consistently;
- retain hostname to protect subdomain boundaries;
- strip query strings;
- normalize trailing slashes;
- compare normalized `(host, path)` pairs.

Do not join on path alone. The handover explicitly warns that doing so can mix
subdomains or tenants on a shared property.

Each mapping must record:

- original URL;
- normalized host/path;
- mapping method;
- mapping status;
- mapping confidence;
- rejection reason, if any.

### 9.2 Query matching

An AI monitoring prompt is often longer than a real Google query. Matching must
therefore be explicit:

1. Exact text match.
2. Normalized exact match.
3. Existing query-cluster mapping from Query Finder/upstream cluster data.
4. Manually approved mapping.
5. Unmapped.

Do not silently fuzzy-match a long AI prompt to a GSC query and then attribute
traffic or revenue to it. Optional similarity can be presented as a candidate
for review, not accepted automatically.

### 9.3 Mapping status

Use a closed status set:

- `exact`;
- `cluster_mapped`;
- `manually_approved`;
- `page_only`;
- `query_only`;
- `unmapped`;
- `rejected_cross_tenant`.

## 10. Metrics and calculations

### 10.1 GSC metrics

For the mapped queries/pages, calculate:

- impressions;
- clicks;
- CTR;
- average position;
- distinct queries;
- distinct pages.

Aggregation rules:

- Sum impressions and clicks.
- Recalculate CTR as `total clicks / total impressions * 100`; never average
  stored row-level CTR values.
- Weight average position by impressions; never use an unweighted average of
  row positions.
- Keep country/device breakdowns optional and separate from headline totals.

### 10.2 GA4 metrics

For the mapped client-owned landing pages, calculate:

- sessions;
- engaged sessions;
- engagement rate;
- total users;
- new users;
- conversions;
- revenue, when present and eligible;
- AI-referral sessions and revenue;
- organic sessions and revenue;
- unattributed sessions and revenue.

Aggregation rules:

- Sum additive metrics.
- Recalculate engagement rate from aggregate engaged sessions and sessions.
- Preserve currency with revenue.
- Never combine different currencies into one total without an explicit FX
  policy and version.

### 10.3 Delta calculation

For every metric store:

```json
{
  "baseline": 100,
  "follow_up": 120,
  "absolute_delta": 20,
  "relative_delta": 0.2,
  "unit": "count"
}
```

When the baseline is zero:

- preserve the absolute delta;
- set relative delta to `null`;
- label the result `new_activity` when appropriate;
- never report infinite or arbitrarily capped growth.

For average position, a smaller number is an improvement. Store the arithmetic
delta but apply direction-aware interpretation.

## 11. Outcome classification

Do not reduce all evidence to an unexplained single score in the first version.
Produce a transparent component scorecard and one conservative classification.

Suggested outcome statuses:

- `waiting_for_window`;
- `source_not_connected`;
- `source_stale`;
- `unmapped`;
- `insufficient_data`;
- `ai_visibility_only`;
- `search_growth_only`;
- `website_growth_only`;
- `associated_downstream_growth`;
- `mixed_result`;
- `no_material_change`;
- `associated_decline`;
- `inconclusive`.

Example classification logic:

- AI visibility improved, GSC improved, and relevant GA4 page metrics improved:
  `associated_downstream_growth`.
- AI visibility improved but GSC/GA4 stayed flat: `ai_visibility_only`.
- Search improved but GA4 did not: `search_growth_only` or `mixed_result`.
- Sources are incomplete, stale, or unmapped: `inconclusive`.

Materiality thresholds must be configuration-driven, versioned, and tested.
The report must show the actual metrics so the classification is auditable.

## 12. Causality and language policy

Allowed language:

- "followed by";
- "associated with";
- "moved in the same direction";
- "consistent with";
- "no measurable downstream change was observed".

Disallowed without stronger evidence:

- "caused";
- "generated this revenue";
- "produced these conversions";
- "proved ROI".

Confidence can be strengthened when all of these are available:

- exact client/query/page identity;
- complete baseline and follow-up windows;
- confirmed execution timestamp;
- comparable monitoring configuration;
- stable source freshness;
- page snapshots demonstrating the intended change;
- control pages or historical seasonal baselines.

Even then, a before/after observation is not a randomized causal experiment.

## 13. Proposed database model

All new tables belong to this project and use the existing `ai_visibility_*`
namespace. Existing GSC/GA4 tables remain owned by the analytics system and are
read-only to this module.

### 13.1 `ai_visibility_measurement_plans`

Suggested fields:

- `id uuid primary key`;
- `idempotency_key text unique`;
- `report_id uuid`;
- `client_id uuid not null`;
- `signal_id text not null`;
- `anchor_type text`;
- `anchor_at timestamptz`;
- `policy_version text`;
- `baseline_start/end date`;
- `follow_up_start/end date`;
- `target_queries jsonb`;
- `target_pages jsonb`;
- `mapping_details jsonb`;
- `required_sources text[]`;
- `status text`;
- `created_at/updated_at timestamptz`.

The idempotency key should be derived from client, report, signal, anchor,
targets, and policy version.

### 13.2 `ai_visibility_measurement_snapshots`

Suggested fields:

- `id uuid primary key`;
- `plan_id uuid not null`;
- `source text` (`gsc` or `ga4`);
- `window_type text` (`baseline` or `follow_up`);
- `requested_start/end date`;
- `effective_start/end date`;
- `fresh_through date`;
- `row_count integer`;
- `metrics jsonb`;
- `source_status text`;
- `warnings jsonb`;
- `payload_checksum text`;
- `captured_at timestamptz`;
- unique `(plan_id, source, window_type, payload_checksum)` or an equivalent
  versioned upsert policy.

Store aggregates and provenance, not OAuth credentials and not unnecessary raw
analytics rows.

### 13.3 `ai_visibility_measurement_outcomes`

Suggested fields:

- `id uuid primary key`;
- `plan_id uuid unique not null`;
- `classification text`;
- `confidence text`;
- `gsc_deltas jsonb`;
- `ga4_deltas jsonb`;
- `evidence_summary jsonb`;
- `warnings jsonb`;
- `evaluated_at timestamptz`;
- `algorithm_version text`.

Regeneration should update the same outcome for the same plan/version rather
than creating accidental duplicates.

## 14. Proposed backend modules

```text
src/ai_visibility/measurement/
  models.py          # strict Pydantic request/snapshot/outcome models
  client.py          # authenticated calls to the safe analytics endpoint
  planning.py        # signal -> measurement plan
  mapping.py         # query/page matching with explicit statuses
  aggregation.py     # deterministic metric aggregation
  outcomes.py        # deltas and conservative classifications
  persistence.py     # namespaced plan/snapshot/outcome writes
  reports.py         # Growth Impact JSON and Markdown
```

Keep this package independent from report generation. The existing command
must not import Google client libraries or wait for analytics windows.

## 15. Proposed CLI

Names are provisional:

```powershell
# Create plans from an existing report without calling Google
uv run ai-visibility measurement plan --company "Aprio"

# Show waiting/ready/incomplete plans
uv run ai-visibility measurement status --company "Aprio"

# Capture available baseline/follow-up aggregates from the safe backend API
uv run ai-visibility measurement capture --company "Aprio"

# Evaluate plans that have complete snapshots
uv run ai-visibility measurement evaluate --company "Aprio"

# Generate JSON and Markdown outcome report
uv run ai-visibility measurement report --company "Aprio"
```

An optional orchestration command can later combine capture, evaluation, and
reporting. It must return partial status honestly when one source is missing.

## 16. Growth Impact Report contract

Suggested top-level structure:

```json
{
  "report_type": "growth_impact_report",
  "schema_version": "1.0.0",
  "client": {},
  "source_intelligence_report": {},
  "measurement_period": {},
  "measurement_status": "complete|partial|waiting|inconclusive",
  "executive_summary": {},
  "outcomes": [],
  "gsc_summary": {},
  "ga4_summary": {},
  "unattributable_ai_traffic": [],
  "data_quality_flags": [],
  "methodology": {}
}
```

Each outcome must include:

- source signal and report IDs;
- monitored topic/query/cluster;
- mapped client pages and mapping method;
- anchor type and time;
- baseline/follow-up windows;
- source freshness;
- raw aggregate metrics and deltas;
- classification and confidence;
- warnings and missing evidence;
- a plain-language, non-causal interpretation.

## 17. Existing-system risks that must be handled

The supplied handover identifies several behaviors that affect reliability.

### 17.1 Cron dependency

The raw metric tables are reliably warmed only when the external daily cron
calls `/revenue/cron/sync`. Dashboard-driven writes are not sufficient.

Before this module is enabled:

- verify a scheduler is configured;
- monitor its last successful completion;
- expose freshness per client/source;
- prevent stale tables from being presented as zero growth.

### 17.2 Fire-and-forget writes

Some existing service reads schedule background upserts and return before the
database write completes. Failed writes may only appear in logs.

The measurement module should consume a response with explicit freshness and
row counts. It must not assume that a successful dashboard/API response means
the persistent table was updated.

### 17.3 Cache behavior

Cache hits can bypass persistence, caches are per process, and reconnecting one
client clears the global in-process cache. Measurement should use source dates
and checksums rather than infer freshness from request success.

### 17.4 Shared properties and fail-open behavior

The handover notes that GA4 domain filtering can fail open when the client has
GA4 connected but no GSC site configured. The new internal endpoint must change
this behavior for measurement requests: absent domain scope must be an error or
explicitly unavailable state, never unrestricted data.

### 17.5 Data delay

Both integrations clamp the effective end date to two days before today. The
measurement plan and user-facing report must display effective dates and avoid
evaluating a follow-up window too early.

### 17.6 GSC row ceiling

The current GSC query/page fetch stops at 100,000 rows. The endpoint must return
a truncation warning when that ceiling is reached. A truncated response cannot
receive normal/high measurement confidence.

### 17.7 Unencrypted refresh tokens

The source system currently stores long-lived refresh tokens as plaintext in
`user_metrics`. Token encryption is an important hardening item for the source
system, but it is not owned by this measurement project. This project reduces
risk by never reading that table or handling tokens.

## 18. Security requirements

- Never log or return Google authorization codes, access tokens, or refresh
  tokens.
- Never expose `SUPABASE_SECRET_KEY` or Google client secrets to this module's
  output.
- Use a dedicated service-to-service credential for the safe measurement API.
- Authorize exact client scope on every request.
- Reject admin-style client overrides unless the service identity is explicitly
  permitted and audited.
- Fail closed when tenant/domain/property mapping is incomplete.
- Use explicit response schemas; ignore/reject unexpected secret-like fields.
- Apply request timeouts, bounded retries, and idempotency.
- Avoid recording raw search queries containing apparent personal data in logs.
- Treat GSC queries, page paths, conversion counts, and revenue as sensitive
  client data.
- Keep all new database writes in `ai_visibility_*` tables.
- Preserve `public.ai_monitoring` as immutable and read-only.

## 19. Failure handling

| Condition | Required behavior |
|---|---|
| GSC disconnected | Continue GA4-only; mark GSC unavailable |
| GA4 disconnected | Continue GSC-only; mark GA4 unavailable |
| Both disconnected | Keep plan; report source-not-connected |
| Cron stale | Do not evaluate as zero; mark source stale |
| Follow-up incomplete | Keep waiting status |
| Query unmapped | Use page-only result or mark unmapped |
| Page unmapped | Use query-only GSC result or mark unmapped |
| Shared-property scope unsafe | Reject source snapshot |
| Analytics API timeout | Retry within bounds; retain previous snapshot |
| Partial source response | Persist with warnings and partial status |
| Baseline zero | Absolute delta only; relative delta null |
| Currency missing/mixed | Do not aggregate revenue |
| GSC truncated | Flag truncation and reduce confidence |
| Client ID mismatch | Reject entire response |

One source failing must not delete or overwrite a previously valid snapshot
from the other source.

## 20. Observability

Emit structured logs for:

- plan creation and idempotent reuse;
- measurement API request outcome;
- client ID and plan ID, but no secrets;
- requested and effective date windows;
- source freshness;
- row counts and truncation;
- mapping method and result count;
- snapshot checksums;
- outcome classification and algorithm version;
- partial/inconclusive reasons;
- cross-tenant rejection events.

Recommended operational metrics:

- active measurement plans;
- plans waiting, ready, measured, partial, and failed;
- source connection rate;
- source freshness lag;
- safe mapping rate;
- cross-tenant/unsafe-scope rejection count;
- GSC truncation count;
- outcome distribution;
- percentage of outcomes anchored to confirmed execution versus report time.

## 21. Test strategy

### 21.1 Unit tests

- URL normalization preserves host and path correctly.
- Subdomains never collapse into the parent domain.
- Query matching follows the declared priority.
- Fuzzy candidates are never automatically accepted.
- CTR is recomputed from totals.
- position is impression-weighted.
- engagement rate is recomputed from totals.
- average-position direction is interpreted correctly.
- zero baselines produce `relative_delta = null`.
- missing values are not converted to zero.
- currency mismatches block revenue aggregation.
- outcome classifications are deterministic and versioned.
- report-only anchors are labeled observation-only.

### 21.2 Contract tests

- Request and response JSON validate against schemas.
- Unexpected secret/token fields are rejected.
- Client-ID mismatches are rejected.
- Effective windows and freshness are required.
- Partial source states round-trip correctly.
- Schema-version incompatibility fails clearly.

### 21.3 Tenant-isolation tests

- Two clients share one GSC site.
- Two clients share one GA4 property.
- Two clients share the same landing-page path on different hosts.
- GA4 is connected while GSC/domain scope is missing.
- Non-admin/service identity attempts a client override.
- Returned rows contain a different client than requested.

Every case must fail closed or return only the authorized client's aggregate.

### 21.4 Integration tests

- GSC-only plan from report to outcome.
- GA4-only plan from report to outcome.
- Combined complete plan.
- Waiting plan with incomplete follow-up window.
- Stale cron/source state.
- API timeout and bounded retry.
- GSC 100,000-row truncation warning.
- Fire-and-forget persistence lag represented as stale/partial.
- Idempotent re-capture and re-evaluation.
- Existing report generation works unchanged with measurement disabled.

### 21.5 Acceptance fixture

Use an Aprio fixture with:

- one material AI visibility signal;
- one exact client-owned page;
- one third-party citation page that must be excluded from GA4 matching;
- multiple GSC queries mapped to the topic;
- baseline/follow-up GSC aggregates;
- baseline/follow-up GA4 landing-page aggregates;
- some AI-referral traffic;
- an incomplete or missing optional revenue field.

The expected output must be deterministic JSON plus Markdown.

## 22. Implementation phases

### Phase 0: prerequisites after Recon integration

- Finalize the shared signal bundle.
- Add missing query, cluster, time, and target-page identity to signals.
- Establish exact client-ID mapping across systems.
- Verify the daily Google sync cron is operating.
- Define the safe service-to-service authentication approach.
- Confirm analytics ownership and metric definitions with the existing backend
  team.

Exit criteria: a signal can identify one client, topic, period, and measurable
target without parsing prose.

### Phase 1: contracts and offline planning

- Add Pydantic models and JSON Schemas.
- Build plan creation and target mapping.
- Add migrations for plans/snapshots/outcomes.
- Implement fixtures and unit tests.
- Add `measurement plan` and `measurement status` commands.

No Google or analytics calls are required in this phase.

Exit criteria: Aprio report deterministically produces valid measurement plans.

### Phase 2: GSC measurement first

- Add or consume the safe internal endpoint for GSC aggregates.
- Implement baseline/follow-up capture.
- Calculate GSC deltas and statuses.
- Generate a GSC-only Growth Impact Report.

GSC is the first source because query/page mapping is closest to the AI report's
topic structure and does not require conversion/revenue interpretation.

Exit criteria: one complete GSC outcome is generated without direct credential
or raw table access.

### Phase 3: GA4 landing-page measurement

- Add GA4 aggregate response to the safe endpoint.
- Enforce domain scoping for shared properties.
- Add traffic, engagement, conversion, and optional revenue deltas.
- Add AI-referral and unattributable-AI-traffic sections.

Exit criteria: GA4 outcomes are tenant-safe and can operate independently of
GSC availability after safe scope has been established.

### Phase 4: combined outcome classification

- Combine AI, GSC, and GA4 evidence.
- Implement conservative classifications and confidence.
- Generate complete JSON/Markdown reports.
- Add structured observability and operational runbooks.

Exit criteria: combined results expose all component metrics and never overstate
causation.

### Phase 5: advanced evaluation

Optional later improvements:

- confirmed action-completion anchors;
- control pages;
- year-over-year seasonal baselines;
- interrupted time-series analysis;
- repeated measurement windows;
- recommendation-type effectiveness calibration;
- client-level benchmarking with privacy-safe aggregation.

These should follow, not block, the transparent baseline/follow-up MVP.

## 23. Acceptance criteria

The first production version is complete when:

1. Existing AI report generation remains unchanged and independently usable.
2. Measurement can be fully disabled with no analytics imports or calls.
3. This project never reads or handles Google OAuth credentials.
4. Every analytics request and stored outcome is bound to an exact client ID.
5. Shared site/property tests demonstrate no cross-client leakage.
6. GSC and GA4 can each degrade independently.
7. Baseline/follow-up windows are equal and display effective source dates.
8. The two-day Google data lag is enforced.
9. Metric aggregation follows documented formulas.
10. Missing and zero values are distinct.
11. Third-party citation pages never become client GA4 targets.
12. Results contain raw aggregate values, deltas, mapping details, freshness,
    warnings, and algorithm version.
13. Output uses association language and never asserts unsupported causation.
14. Plan creation, capture, and evaluation are idempotent.
15. JSON Schema, unit, integration, tenant-isolation, lint, and type checks pass.
16. The Aprio fixture generates deterministic JSON and Markdown Growth Impact
    Reports.

## 24. Decisions required before implementation

Resolve these after Recon V1 integration:

1. Confirm whether the safe internal API or restricted-view fallback will be
   used.
2. Define service-to-service authentication and authorization.
3. Confirm the authoritative cross-system `client_id` mapping.
4. Confirm whether existing Query Finder clusters map directly to
   `public.ai_monitoring.cluster_id`.
5. Decide how explicit client-owned target pages enter the unified signal
   bundle.
6. Confirm supported GA4 conversion and revenue definitions per client.
7. Define currency behavior.
8. Choose materiality thresholds for GSC and GA4 outcomes.
9. Decide whether report-time anchors are sufficient for MVP or whether an
   external action-completion event is required.
10. Confirm cron ownership, schedule, monitoring, and freshness SLA.

## 25. Recommended first deliverable

After Recon integration, implement a GSC-only proof of concept:

1. Take one persisted Aprio AI visibility report.
2. Create plans for signals with a safely mapped client-owned page or query
   cluster.
3. Request two 28-day GSC aggregate windows through the safe API.
4. Calculate impressions, clicks, CTR, and weighted-position deltas.
5. Produce a small JSON/Markdown Growth Impact Report.
6. Validate tenant isolation, freshness, mapping, and non-causal language.

Only after that path is trustworthy should GA4 traffic, conversions, and
revenue be added.

