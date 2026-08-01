# Attribution and Confidence

## Objective

Explain why visibility likely changed without overstating causation.

## Candidate explanation types

- direct_page_content_change
- new_owned_source
- expanded_owned_source
- new_third_party_mention
- expanded_third_party_mention
- competitor_source_gain
- competitor_source_loss
- recommendation_pattern_shift
- broader_source_portfolio_shift
- provider_retrieval_shift
- provider_divergence
- execution_configuration_change
- data_quality_anomaly
- insufficient_evidence

## Evidence gates

A page may be a direct driver only when strong evidence exists, such as:

- historical page diff added or strengthened the company
- a new third-party page meaningfully recommends the company
- a new owned page has material answer coverage
- answer passage semantically aligns with the page’s company content
- pattern repeats across independent comparisons

A page already cited broadly before the company appeared should default to:

- supporting source
- topic-context source
- or unknown contributor

## Initial scoring model

Positive evidence:

- +20 page meaningfully mentions company
- +10 company in title/heading
- +10 links to official domain
- +10 publisher is company
- +20 page diff added/strengthened company
- +10 URL is new or materially expanded
- +10 positive association lift
- +10 answer/page semantic alignment
- +10 repeated across queries/providers/runs

Penalties:

- -25 page does not mention company and is not company-owned
- -20 URL appears in at least 80% of answers
- -15 historical snapshot unavailable
- -10 citation positions unavailable
- -15 configuration comparability incomplete
- -10 only one answer of evidence
- -10 unresolved metric mismatch

Clamp to 0–100.

Suggested bands:

- 70–100 high
- 45–69 medium
- 20–44 low
- 0–19 unrelated/insufficient

Store all components. Do not expose only a black-box score.

## Language policy

High:

> The page change is a likely direct driver.

Medium:

> The source is a likely contributor.

Low:

> The source is associated with the change.

Unknown:

> Evidence is insufficient to determine why.

Never use “caused” for low or medium evidence.

## Alternative explanations

Every material signal should consider:

- provider model or retrieval behavior changed
- prompt or pipeline configuration changed
- source weighting changed
- answer bundle became repetitive
- company extraction changed
- competitor content displaced client content
- page changed but historical snapshot unavailable
- random provider volatility

## Aprio expected classification

Primary:

```text
recommendation_pattern_shift
confidence: medium
```

Related:

```text
expanded_owned_supporting_source
confidence: low-to-medium
```

New AI/token article:

```text
localized_new_owned_source
overall contribution confidence: low
```

Single-page causation:

```text
low
```
