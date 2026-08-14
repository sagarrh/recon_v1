# Recon Outcome Measurement Plan (GSC + GA4)

Status: implementation specification
Scope: GSC and GA4 only. **CRM is explicitly out of scope.**
Supersedes: the revenue direction currently encoded in `src/scout/revenue.py`
Companion: `POST_REPORT_GSC_GA4_MEASUREMENT_PLAN.md` (reused, not replaced — see §2)

---

## 1. Context

Recon currently converts SOV movement into a forward-looking dollar estimate at
recommendation time. That produces impressive numbers with no defensible basis.

Recon's job becomes:

> Find commercially important visibility problems, recommend a concrete
> intervention, track whether it was executed, and measure whether relevant
> demand, engagement, and commercial actions improved afterward.

The value loop:

```text
Detect → Prioritize → Recommend → Execute → Measure → Learn
```

Revenue becomes the final evidence layer, never the first estimate.

### What is being removed and why

`compute_cluster_revenue` ([revenue.py:220-230](../../../src/scout/revenue.py#L220)) computes:

```python
addressable = rev_usd / client_share_frac
opp = addressable * (1 - client_share_frac)
```

At 5pp SOV this reports an "opportunity" of **19× the client's own revenue**;
at 1pp, 99×. The figure is driven by the size of the denominator, not by
anything commercial. Two supporting defects:

- [revenue.py:185](../../../src/scout/revenue.py#L185) calls
  `actual_revenue_from_ga4(ga4_rows=ga4)` **without** the `landing_pages` filter
  the function supports — property-wide revenue is labelled cluster revenue.
- [revenue.py:156](../../../src/scout/revenue.py#L156) `resolve_basis` returns
  `"actual"` when only GSC impressions exist.

### Accepted limitation of the GSC+GA4-only scope

Without CRM, the measurable funnel ends at **commercial intent** (GA4 key
events: demo requests, form submits, trial registrations, quote requests).

- **Ecommerce clients** — recorded revenue is available via GA4 purchase
  revenue on target landing pages.
- **B2B / professional-services clients** — revenue is
  **permanently `unavailable`** under this scope. They measure to leads and
  qualified actions only.

This is a deliberate accepted constraint, not a gap to be filled with a
modeled number. Sign off on it before Phase 1 starts.

---

## 2. Relationship to the existing measurement plan

`POST_REPORT_GSC_GA4_MEASUREMENT_PLAN.md` is a thorough, still-valid spec for
the GSC/GA4 measurement engine. **Reuse it wholesale.** Do not redesign:

| Reuse from existing spec | Section |
|---|---|
| Safe internal measurement API boundary (no OAuth/token handling here) | §6 |
| Equal 28-day baseline/follow-up windows, 2-day Google lag clamp | §8.3 |
| Page normalization (host+path, never path alone) and query matching priority | §9 |
| Closed mapping status set | §9.3 |
| Aggregation rules — CTR recomputed from totals, impression-weighted position | §10.1-10.2 |
| Delta shape, zero-baseline handling | §10.3 |
| Outcome classification statuses | §11 |
| Causality language policy | §12 |
| Failure-handling matrix | §19 |
| Tenant-isolation test requirements | §21.3 |
| Acceptance criteria | §23 |

**Three things this plan changes in it:**

1. **Its open decision §24.9 is now closed.** Report-time anchors are *not*
   sufficient. Confirmed execution timestamps are required, and Recon owns
   producing them.
2. **Plans get a second source.** The existing spec creates measurement plans
   from citation-report signals. Recon *recommendations* become an equal plan
   source. One shared engine, two subject types.
3. **CRM/attribution_events paths are dropped**, not deferred.

---

## 3. Architectural decisions

### 3.1 One measurement engine, two subject types

Build the engine once at `src/ai_visibility/measurement/` per the existing spec
§14. Plans carry a polymorphic subject:

```text
subject_type: "citation_signal" | "recon_recommendation"
subject_id:   text
```

Rationale: window logic, page normalization, query mapping, aggregation, and
tenant safety are identical for both. Duplicating them into `scout/` would
create two divergent implementations of the riskiest code in the system.

### 3.2 All new DDL lives in this repo's `migrations/`

**Important discovery:** this repo's `migrations/` creates only
`ai_visibility_*` and `aivc_*` tables. There is **no `scout_*` DDL anywhere in
this repository** — `scout_outcomes`, `recommendations`, `scout_assets` etc. are
created externally on the Supabase side.

To keep this plan executable without an external DDL dependency, **all new
tables use the `aivc_*` / `ai_visibility_*` namespace** and ship in
`migrations/0008_*.sql`. `validate_same_project()`
([settings.py:47](../../../src/aivc/config/settings.py#L47)) already guarantees
the direct-PG connection and Supabase point at the same project, so Scout can
read them via supabase-py where needed.

Consequence: the execution record is written by the **`aivc` CLI** (direct
psycopg), not by Scout's supabase-py writer. This is simpler and keeps CS
tooling in one place.

### 3.3 Outcome signals need their own bundle

`build_recon_bundle` sets `AnalysisPeriod` to a single `sync_date`
([recon_bundle.py:155-158](../../../src/aivc/producers/recon_bundle.py#L155)).
An outcome for a recommendation implemented 8 weeks ago does not belong in a
detection bundle.

Emit a **separate outcome bundle** with `analysis_period` spanning
baseline_start → follow_up_end, under the same parent lineage.
`MeasuredMetric.unit` already includes `"usd"`
([models.py:57](../../../src/aivc/contracts/models.py#L57)), so
`SignalBundle.schema_version` stays `"1.0"`.

### 3.4 Fix the untyped recommendations passthrough

[recon_bundle.py:143](../../../src/aivc/producers/recon_bundle.py#L143):

```python
recommendations = [_dump(item) for item in state.get("recommendations", [])]
```

`SignalBundle.recommendations` is `list[dict[str, Any]]` — unvalidated. Every
`Recommendation` field flows into a checksummed bundle unexamined, including
the revenue fields. Replace with a typed `RecommendationRef` contract model in
`aivc/contracts/models.py`. Do this in Phase 1 regardless of the rest.

---

## 4. Phase 1 — Foundation

**Goal:** stop emitting indefensible numbers; make every recommendation a
measurable hypothesis with a recorded execution.

Nothing in this phase calls Google. It is entirely offline.

### 1.1 Remove the SOV→dollar path

| File | Change |
|---|---|
| `src/scout/revenue.py` | Delete `revenue_opportunity()` and the `addressable = rev_usd / client_share_frac` step. Delete `allocate_cluster_revenue()`. Keep `revenue_at_risk()` **internal-only**, clearly labelled experimental. |
| `src/scout/revenue.py:185` | Pass `landing_pages=` to `actual_revenue_from_ga4`. Return `None` when no target pages are mapped — never fall back to property-wide. |
| `src/scout/revenue.py:156` | `resolve_basis` → replaced by the category resolver in 1.2. |
| `src/scout/revenue.py:5` + `config.py:153` | Collapse the duplicated `0.15` capture fraction to one source of truth; then delete it with `modeled_revenue_from_demand`. |
| `src/scout/reports/asset_attribution.py` | Remove Channel B (`attribution_events`, lines ~305-345). Assets with no observable link get `attribution_status = "insufficient_linkage"`, not an allocated share. |
| `src/scout/config.py` | Delete `revenue_ai_referral_capture_fraction`, `geo_crm_enabled`, `crm_lag_penalty_*`. Keep `revenue_layer_enabled` as the kill switch during migration, then remove. |

`geo_financials_enabled` (default `True`) may stay: it feeds only raw AOV/CR/CTR
into the **internal** report prompt
([report_gen.py:380-386](../../../src/scout/nodes/report_gen.py#L380)), and
`_client_geo_block` carries no revenue. Verify this remains true after the
prompt changes in 1.3.

### 1.2 Revenue category taxonomy

Replace `revenue_basis: ^(actual|modeled|hybrid|none)$` with:

```text
recorded              — GA4 purchase revenue on mapped target landing pages
influenced            — GA4 recorded revenue linked to target pages/sessions
incremental_estimate  — Phase 5 only; requires a control or seasonal baseline
modeled_scenario      — assumption-driven planning figure; NOT revenue
unavailable           — insufficient evidence (the honest default)
```

Rules, enforced in code and tested:

- Categories are **never summed**. No aggregate field may combine them.
- `modeled_scenario` is **internal-only by default**, behind
  `modeled_scenario_client_visible: bool = False`, and must never render in the
  same view as `recorded` or `influenced`.
- For non-ecommerce clients the resolver returns `unavailable` for revenue and
  reports the commercial-intent layer instead.

Touch points: `models/recommendation.py:36`, `models/sov.py:69`,
`db/sed_writer.py:334-336,469-470`, `builders/intake.py:104-105`,
`builders/handoff.py:52-53`, `nodes/report_gen.py:120`,
`reports/asset_attribution.py:140`.

### 1.3 Structured targets on every recommendation

`Recommendation` ([models/recommendation.py](../../../src/scout/models/recommendation.py))
today carries only free-text `action_bullets`. Add:

```python
target_pages: list[str]          # client-owned URLs only
target_queries: list[str]
action_type: str                 # content_update | new_page | schema | ai_access | third_party | other
mapping_confidence: str          # exact | cluster_mapped | unmapped
expected_leading_outcome: str    # e.g. "AI citations + GSC impressions"
expected_business_outcome: str   # e.g. "qualified visits, demo requests"
```

These come from the **structured JSON schema** the LLM returns
([recommendation_gen.py:229-243](../../../src/scout/nodes/recommendation_gen.py#L229)) —
never parsed out of prose. Validate every returned URL against the client's
known owned domains; reject third-party URLs (the existing spec §8.4 is explicit
that citation URLs must never become GA4 targets). Unvalidatable →
`mapping_confidence = "unmapped"` and the recommendation is not measurable.

### 1.4 Commercial priority score

Net-new. Confirmed: priority today comes from
`_priority_for_trigger` ([recommendation_gen.py:279](../../../src/scout/nodes/recommendation_gen.py#L279))
via `shift_type`, and **nothing ranks on revenue** — so there is no ordering
regression to manage.

Transparent, additive, no dollars. Component inputs:

```text
business_relevance      commercial_intent       search_demand (GSC impressions)
client_visibility       competitor_advantage    ability_to_win
landing_page_available  evidence_confidence     implementation_effort (inverse)
```

Emit the component vector alongside the total so the score is auditable.
Weights live in `config/reporting.toml` with a `priority_score_version`.
This gates which signals justify investigation and action.

### 1.5 Execution record — the crux

New table `aivc_action_executions` (migration `0008`):

```text
id uuid pk
subject_type text        -- 'recon_recommendation'
subject_id text          -- recommendation_id
client_id uuid not null
status text              -- proposed | accepted | executed | verified | abandoned
implemented_at timestamptz
implemented_by text
target_pages jsonb
target_queries jsonb
action_type text
implementation_notes text
evidence_urls jsonb
verification_status text -- unverified | snapshot_confirmed | manual_confirmed
created_at / updated_at timestamptz
unique (subject_type, subject_id)
```

RLS policies matching `0002_tenant_rls.sql`.

CS-facing CLI on the `aivc` app:

```powershell
uv run aivc execution record --recommendation-id <id> --implemented-at 2026-09-14 --by "cs@example.com"
uv run aivc execution list --company "Aprio" --status executed
uv run aivc execution verify --recommendation-id <id>
```

Optional in Phase 1, valuable later: `verification_status = snapshot_confirmed`
can reuse the existing page-snapshot diff machinery
(`ai_visibility/scraping/snapshots.py`) to confirm the target page actually
changed — turning a self-reported claim into observed evidence.

### 1.6 Re-anchor outcome measurement

[outcome_measure.py:104](../../../src/scout/db/outcome_measure.py#L104):

```python
window_elapsed_at = wd + timedelta(weeks=window_weeks)   # wd = recommendation week_date
```

Must key off `implemented_at` from `aivc_action_executions`. Recommendations
with no execution record are **not measured** — they report
`awaiting_execution`, not a null outcome.

[outcome_measure.py:156](../../../src/scout/db/outcome_measure.py#L156)
`"executed": None` becomes the real status. Remove the
`get_attribution_revenue` call at line 122 (CRM, out of scope).

Delete `_TIMELINE_WINDOW_WEEKS` (lines 15-23) once legacy rows have aged out —
`Recommendation.window_weeks` is already the structured primary and
`outcome_measure` already prefers it (lines 101-103).

### 1.7 Measurement plan record

New table `ai_visibility_measurement_plans` — use the existing spec §13.1
schema, plus `subject_type` / `subject_id` from §3.1 above. Written at
recommendation time, before execution. Pre-registering the plan is what stops
the measurement window from being chosen after the outcome is known.

### Phase 1 exit criteria

- No client-facing output contains a SOV-derived dollar figure.
- Every publishable recommendation carries validated target pages/queries or is
  explicitly `unmapped`.
- A CS user can record an execution and see it reflected in outcome status.
- `outcome_measure` anchors on `implemented_at` and distinguishes
  `awaiting_execution` from `measured`.
- `aivc report generate` output is unchanged in structure apart from the
  removed revenue fields and added targets/priority.

---

## 5. Phase 2 — GSC outcome measurement

Follow the existing spec §9, §10.1, §10.3, §11 exactly. Recon-specific deltas:

- Plans are anchored on `implemented_at`, so results are **not**
  `observation_only` — that label now applies only to plans anchored on report
  time (citation-signal plans without an execution record).
- Measure at the recommendation's `target_queries` and `target_pages` grain.
- Metrics: impressions, clicks, CTR (recomputed from totals),
  impression-weighted average position, distinct queries/pages.
- Snapshots into `ai_visibility_measurement_snapshots` (existing spec §13.2)
  with `payload_checksum`, `fresh_through`, `row_count`, warnings.

**Exit:** one complete GSC outcome, end to end, for an executed Aprio
recommendation — with no direct credential or raw-table access.

---

## 6. Phase 3 — GA4 outcome measurement

Follow the existing spec §10.2. Recon-specific deltas:

- Engagement layer: sessions, engaged sessions, engagement rate (recomputed
  from totals), users.
- **Commercial-intent layer** (this is the terminal layer for B2B): key events —
  demo requests, contact forms, trial registrations, quote requests. Report as
  counts and conversion rates against sessions.
- **Revenue layer** — ecommerce only: GA4 purchase revenue on mapped target
  landing pages → category `recorded` or `influenced`. Currency preserved;
  never aggregate mixed currencies (existing spec §19).
- Enforce domain scoping for shared properties — the existing spec §17.4 notes
  GA4 filtering can fail open when GSC is unconfigured. **Fail closed.**

**Exit:** GA4 outcomes are tenant-safe, degrade independently of GSC, and a
B2B client correctly reports commercial intent with `revenue: unavailable`.

---

## 7. Phase 4 — Combined outcome, cards, and the learning loop

### 7.1 Funnel reporting

Report each layer separately; never infer a lower layer from a higher one:

```text
AI visibility        ✓  24% → 39%
Search demand        ✓  impressions +31%, clicks +22%, position 11.2 → 7.8
Website engagement   ✓  sessions +18%
Commercial intent    ✓  demo requests 9 → 14
Revenue              —  unavailable (no ecommerce data for this client)
```

A layer with no data renders as `unavailable`, never as zero.

### 7.2 Outcome card

One compact card per executed recommendation: action, implemented date, each
funnel layer with before→after, assessment, confidence, and an explicit
**"why confidence is not high"** line naming the missing evidence. Rendered in
`aivc/reporting/` alongside the existing `DecisionCard`
(`aivc/reporting/cards.py`) — reuse its priority and confidence conventions.

### 7.3 Outcome signal bundle

Per §3.3: a distinct producer run emitting `recon.outcome_measured` signals
with `MeasuredMetric` entries per funnel layer, evidence refs to the snapshots,
and the classification from the existing spec §11.

### 7.4 North-star instrumentation

> **Percentage of executed Recon recommendations that produce a measurable
> positive downstream outcome.**

Track the full funnel drop-off as first-class output, not just the positive
rate:

```text
proposed → accepted → executed → verified → measured → positive
```

Also track, per the user's supporting metrics: execution rate, safely-mapped
rate, visibility improvement rate, GSC improvement rate, conversion improvement
rate, recorded revenue associated with executed actions, % with sufficient
evidence.

**Report the null and negative rates alongside the positive rate.** Without a
control, optimizing the positive rate alone converges on recommending safe
actions in already-growing topics.

**Exit:** a complete outcome card for an executed Aprio recommendation, with
recommendation-type success rates emerging once volume allows.

---

## 8. Phase 5 — Incremental measurement (optional, later)

- Control pages and control topic groups.
- Year-over-year and seasonal baselines.
- Difference-in-differences.
- Repeated outcome windows.
- Recommendation-type effectiveness calibration feeding back into the priority
  score (the existing `db/calibration.py` + `calibration_feedback_enabled`
  pattern is the model to follow).

Only this phase may emit `incremental_estimate`.

---

## 9. Files touched

**Modified — Scout**
`revenue.py` · `models/recommendation.py` · `models/sov.py` ·
`nodes/recommendation_gen.py` · `nodes/report_gen.py` · `db/outcome_measure.py` ·
`db/sed_writer.py` · `db/revenue_context.py` · `reports/asset_attribution.py` ·
`config.py` · `prompts/scout-recommendation-generation*.md`

**Modified — AIVC**
`contracts/models.py` (typed `RecommendationRef`) · `producers/recon_bundle.py` ·
`cli/app.py` (execution commands) · `reporting/cards.py` · `reporting/models.py`

**New**
`migrations/0008_recon_execution_measurement.sql` (mirrored into
`src/ai_visibility/resources/migrations/`) ·
`src/ai_visibility/measurement/` (per existing spec §14) ·
`src/aivc/database/executions.py`

---

## 10. Verification

```powershell
uv run pytest                 # 70% coverage gate
uv run ruff check .
uv run mypy
uv run ruff format --check .
```

**Phase 1 specific**

- Unit: category resolver never sums categories; `modeled_scenario` is excluded
  from client-visible output when the flag is off.
- Unit: target-page validator rejects third-party citation URLs.
- Unit: priority score is deterministic and versioned.
- Unit: `outcome_measure` returns `awaiting_execution` with no execution record,
  and anchors correctly on `implemented_at` when one exists.
- Contract: `SignalBundle` round-trips with typed recommendations; checksum
  stable.
- Regression: `tests/recon/test_p0_correctness.py` and
  `tests/unit/test_recon_bundle.py` still pass.

**End-to-end**

```powershell
uv run aivc db check
uv run aivc run --company "Aprio"
uv run aivc execution record --recommendation-id <id> --implemented-at 2026-09-14 --by "cs@example.com"
uv run aivc report generate --company "Aprio"
```

Confirm: no dollar figure in `report-input-snapshot.json` sourced from SOV;
targets present on every publishable recommendation; execution status visible.

**Phases 2-4** — adopt the existing spec §21 test strategy in full, especially
§21.3 tenant isolation (shared GSC site, shared GA4 property, same path on
different hosts, GA4 connected without GSC scope). Every case fails closed.

---

## 11. Decisions required before starting

1. **Sign off on the B2B limitation** (§1): B2B clients report to commercial
   intent only, with revenue permanently `unavailable` under this scope.
2. **Confirm the measurement API boundary** — existing spec §24.1: safe
   internal FastAPI endpoint (preferred) vs restricted read-only views.
3. **Confirm `aivc_*` namespace for new tables** (§3.2), given no `scout_*` DDL
   exists in this repo.
4. **Who owns the execution record in practice** — CS via CLI is assumed; a UI
   would change the interface but not the schema.
5. **Materiality thresholds** for "measurable positive outcome" per funnel
   layer — needed before the north-star metric means anything.
6. **Whether `modeled_scenario` is ever client-visible.** Recommendation:
   no, at least until Phase 5.
