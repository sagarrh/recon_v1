# Implementation Roadmap

## Phase 0 — Repository audit

Codex must inspect:

- backend
- frontend
- Supabase setup
- migrations
- job framework
- API conventions
- tests
- linting
- deployment
- auth/RLS

Output:

- architecture map
- proposed file list
- dependencies
- risks
- deviations

## Phase 1 — Reliable observation layer

Build:

- stable monitor-query identity
- run validation
- answer normalization
- company registry and aliases
- literal mention calculation
- upstream metric preservation
- citation normalization
- exact URL coverage
- baseline selection
- pairwise deltas
- rolling metrics
- data-quality flags

Deliverable:

Observation-only structured report.

No causal explanations yet.

## Phase 2 — Historical signal engine

Build:

- full provider/query time series
- trends
- volatility
- adjacent comparisons
- rolling baselines
- change points
- provider divergence
- recommendation-pattern detection
- competitor timelines

## Phase 3 — Page evidence

Build:

- priority queue
- secure fetcher
- HTML/PDF extraction
- snapshots
- hashes
- page/company analysis
- page diffs

## Phase 4 — Attribution

Build:

- association lift
- page specificity
- page-change evidence
- hypothesis scoring
- alternatives
- confidence
- deterministic narrative fallback

## Phase 5 — Company Intelligence Report

Build:

- report aggregator
- structured report schema
- executive summary
- provider/query/cluster sections
- competitor section
- source/page section
- timeline
- signals
- actions
- methodology

## Phase 6 — API and UI

Build:

- company-name input
- report status
- report retrieval
- drill-down evidence
- filters
- reprocessing controls
- admin health

## Phase 7 — Hardening

Build:

- backfill
- retry policies
- job observability
- RLS tests
- performance indexes
- caching
- score calibration
- production runbook

## Recommended Codex commit sequence

1. docs/repo audit
2. migrations
3. normalization
4. company metrics
5. citations
6. baseline/comparisons
7. full-history trends
8. recommendation patterns
9. page queue/fetcher
10. page analysis/diffs
11. attribution
12. report schema/aggregator
13. API
14. UI
15. RLS/observability/backfill
16. final docs/tests

## Definition of done

- input is company name only
- full valid history analyzed
- provider/query timelines correct
- competitors included
- exact URL coverage correct
- pages selectively retrieved
- page mentions verified
- page diffs used only when available
- signals confidence-scored
- alternatives shown
- actions shown
- mismatch warnings shown
- processing idempotent
- RLS enforced
- tests and fixtures pass
