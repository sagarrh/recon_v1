# Product Requirements Document

## Product name

AI Visibility Signal Generator / Company Intelligence Report

## Product goal

Given only a client company name, generate a comprehensive intelligence report explaining:

- how the company’s AI visibility changed over its full monitoring history
- which providers, queries, and topic clusters drove the changes
- how competitors gained or lost visibility
- which citation sources appeared, disappeared, or gained influence
- whether cited pages actually mention, recommend, compare, or link to the client or competitors
- what likely caused each important change
- how confident the system is
- what the client should do next

## User input

```json
{
  "company_name": "Aprio"
}
```

The entered company is always a client company.

## Primary output

A detailed `company_intelligence_report` containing:

1. Executive summary
2. Full-period trend
3. Provider intelligence
4. Query intelligence
5. Cluster intelligence
6. Company mention quality
7. Citation intelligence
8. Page/content intelligence
9. Competitor intelligence
10. Change-point timeline
11. Confidence-scored signals
12. Alternative explanations
13. Recommended actions
14. Data-quality and comparability warnings

## Product behavior

The system must:

1. Resolve the client company name to a canonical company and client ID.
2. Resolve aliases and the official domain.
3. Load all configured competitors.
4. Load all monitoring history for the client.
5. Exclude invalid and failed runs.
6. Normalize answers, company mentions, and exact citation URLs.
7. Build provider/query-specific time series.
8. Compare adjacent valid runs and rolling baselines.
9. Detect spikes, declines, sustained trends, reversals, and volatility.
10. Identify exact source changes.
11. Prioritize source pages for retrieval.
12. Verify company mentions on those pages.
13. Compare page snapshots when historical snapshots exist.
14. Generate transparent attribution candidates.
15. Produce a detailed report with evidence and confidence.

## Core distinction

The report must separate:

### Observed

Facts calculated directly from stored data.

Example:

> Aprio appeared in 0 of 20 Gemini answers on June 9 and 14 of 21 answers on July 11.

### Inferred

An explanation supported by evidence but not proven.

Example:

> The increase was likely associated with a recommendation-pattern shift and broader source-portfolio change.

### Unknown

Evidence unavailable or insufficient.

Example:

> The historical version of the page was not stored, so a page-content change cannot be confirmed.

## Success criteria

A report is useful when a user can answer:

- Where are we gaining or losing AI visibility?
- Which provider is responsible?
- Which queries changed?
- Which competitors displaced us?
- Which pages are associated with the change?
- Do those pages actually mention us?
- What changed on those pages?
- Is this a sustained trend or one-off fluctuation?
- What should we do next?
- How trustworthy is the explanation?

## Out of scope for the first release

- Proving legal/scientific causation
- Search-engine ranking diagnostics not available in the monitoring data
- Historical page reconstruction when no archived snapshot exists
- Universal company discovery from arbitrary public input
- Fully automated business decisions without human review
- Treating every mention as positive visibility

## User experience

The simplest interface has:

- one client company input
- optional period selector
- Generate Report button
- report status
- detailed report with drill-down evidence

The default period is the full valid history. Optional filters may narrow the report later.

## Report levels

### Company-level

Aggregates all relevant providers, queries, clusters, competitors, and signals.

### Provider-level

Keeps Gemini, OpenAI, Perplexity, AI Overview, and future providers separate.

### Query-level

Analyzes the same monitored query through time.

### Cluster-level

Aggregates related queries after query-level facts are calculated.

### Run-pair level

Internal evidence unit used to identify when a change first occurred.

## Required signal types

- company_visibility_increase
- company_visibility_decrease
- new_company_appearance
- company_disappearance
- competitor_visibility_gain
- competitor_visibility_loss
- provider_divergence
- recommendation_pattern_shift
- owned_source_gain
- owned_source_loss
- third_party_source_gain
- competitor_source_gain
- citation_coverage_increase
- citation_coverage_decrease
- page_content_change
- company_metric_mismatch
- failed_monitoring_run
- insufficient_attribution_evidence
