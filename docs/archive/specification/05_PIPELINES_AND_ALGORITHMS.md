# Pipelines and Algorithms

## End-product input

```json
{
  "company_name": "Aprio"
}
```

## Report-generation sequence

1. Resolve company to client.
2. Load all valid historical runs for that client.
3. Normalize any unprocessed runs.
4. Build provider/query histories.
5. Compute all adjacent comparisons.
6. Compute rolling baselines.
7. Detect change points and trends.
8. Analyze competitors.
9. Analyze exact citation pages.
10. Retrieve prioritized pages.
11. Analyze page/company relationships.
12. Detect page changes.
13. Score hypotheses.
14. Aggregate into one report.

## Literal company visibility

```text
literal_visibility =
distinct valid answers containing a canonical alias
/
total valid answers
```

Preserve:

- distinct answer count
- total literal mentions
- answer numbers
- context snippets
- upstream count
- upstream visibility

## Baseline selection

### Adjacent valid baseline

For each current run, find the nearest earlier valid run with the same:

- client
- monitor query
- service
- method
- comparable configuration

### Rolling baseline

For report trends, compare the latest valid run against the previous 3–5 comparable valid runs.

Calculate:

- mean
- median
- standard deviation
- min/max
- change from mean
- change from median
- z-score when sample size is adequate

Do not use failed runs.

## Time-series status

Classify:

- increasing
- decreasing
- stable
- volatile
- one-time spike
- one-time drop
- recovering
- newly visible
- disappeared

## Change-point detection

MVP:

- material adjacent delta
- latest value outside prior rolling range
- sustained change in at least two subsequent runs when available

Later:

- statistical change-point method after enough data exists

## Exact URL coverage

Deduplicate one URL per answer.

```text
answer_coverage =
count(distinct answer_number)
```

Keep raw occurrence count separately.

## URL/company association

```text
company_cooccurrence =
answers citing URL and mentioning company
```

```text
cooccurrence_rate =
company_cooccurrence / URL answer coverage
```

```text
company_base_rate =
company answer count / total answers
```

```text
association_lift =
cooccurrence_rate - company_base_rate
```

A ubiquitous source may have high co-occurrence but zero lift.

## Recommendation prominence

Initial deterministic features:

- company in explicit “recommended,” “top,” “examples,” or “who to hire” section
- dedicated bullet or paragraph
- descriptive words near company
- first position in list
- official service description
- generic list mention
- negative/comparative wording

Keep literal visibility and recommendation quality separate.

## Recommendation-pattern shift

For each answer:

1. Extract ordered canonical company set.
2. Group similar sets using Jaccard similarity.
3. Compare bundle frequency over time.

MVP trigger:

- current bundle in at least 40% of answers
- frequency increase at least 25 percentage points

## Competitor analysis

For every configured competitor:

- provider/query visibility timeline
- gain/loss/change point
- source gains
- source losses
- owned source coverage
- third-party mentions
- displacement events
- shared recommendation bundles

## Page prioritization

Queue pages when:

- company visibility changes materially
- URL is new/removed
- URL coverage delta is material
- URL is client- or competitor-owned
- URL strongly co-occurs with changed company answers
- URL is required for a high-severity signal
- recent snapshot is missing

Suggested configurable thresholds:

- visibility delta >= 0.10
- answer count delta >= 2
- URL coverage delta >= 2
- any new/removed owned page
- any new/removed company

## Report aggregation

Aggregate only after provider/query facts exist.

Company-level report metrics should include:

- weighted and unweighted visibility
- provider breakdown
- query breakdown
- cluster breakdown
- latest state
- full-history trend
- largest positive and negative changes
- competitor leaderboard
- source leaderboard
- confidence distribution
- data-quality warnings
