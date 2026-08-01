# Domain Context and Known Facts

## Existing source table

The raw source is `public.ai_monitoring` in Supabase/PostgreSQL.

Important fields:

- `id` — run UUID
- `user_id`
- `client_id`
- `cluster_id`
- `cluster_name`
- `request_payload`
- `answers_list`
- `citations_list`
- `citations_data`
- `companies_data`
- `created_at`

Use the raw table as immutable evidence. Build normalized tables around it.

## JSON structure

### `request_payload`

Known fields:

- `service`
- `method`
- `base_query`
- `user_data.task_id`
- `user_data.user_id`
- `user_data.client_id`

### `answers_list`

Array of generated answers.

### `citations_list`

Array aligned by ordinal position with `answers_list`.

```text
answers_list[0] <-> citations_list[0]
answers_list[1] <-> citations_list[1]
```

### `citations_data`

Domain-level aggregate. It is not a reliable exact page count.

### `companies_data`

Upstream company aggregate. Preserve it, but recompute literal company metrics from answer text.

## PostgreSQL/Supabase notes

- The JSON columns may be `json`; cast to `jsonb`.
- PostgreSQL does not provide `jsonb_object_length`.
- Count JSON object keys with `jsonb_object_keys`.
- Use migrations and server-side service-role access for workers.
- RLS must scope all report data to a client.

## Confirmed comparison rules

A comparable time series must preserve:

- client
- stable monitored query
- service/provider
- method
- execution configuration

Do not combine provider timelines before calculating provider-level changes.

`task_id` is execution-specific.

`cluster_id` is too broad to identify the same monitored query.

## Invalid runs

Runs with zero answers and zero citation groups are failed/empty runs.

They must not be interpreted as genuine zero visibility and must not become baselines.

## Citation counting

Store both:

- raw occurrences
- distinct answer coverage

One URL repeated 20 times in one answer contributes:

- 20 raw occurrences
- 1 answer-coverage unit

## Citation positions

Gemini data may contain:

```text
start_index = 0
end_index = 0
```

Treat those positions as zeroed/unusable.

In that situation, claim only:

> The URL and company appear in the same answer.

Do not claim:

> The URL supports the exact sentence mentioning the company.

## URL normalization

Safe default:

- lowercase hostname
- remove fragment
- normalize trailing slash consistently
- preserve meaningful query parameters

Do not collapse:

- YouTube `v` parameters
- CMS `id` or content parameters
- application route parameters

Only remove known tracking parameters when safe.

## Company canonicalization

A company registry and aliases are required.

Examples:

- BDO / BDO USA
- Redstone GCI / Redstone Government Consulting
- PwC / PricewaterhouseCoopers
- EY / Ernst & Young

## Canonical Aprio case

Client ID:

```text
b88e87f3-0aa5-4da9-be48-2807b12d5a91
```

Query:

```text
Who can help me prepare a certificate of cost and pricing data for a federal contract bid?
```

Provider/method:

```text
gemini / moe
```

Previous run:

```text
fb279a6c-f285-4c8a-afce-78469c9455e1
2026-06-09 20:50:11.141433+00
```

Current run:

```text
1ca57705-51b6-47a6-9725-8d43dbb0a937
2026-07-11 20:54:12.98152+00
```

### Literal company result

Previous:

- 20 answers
- Aprio in 0 answers
- literal visibility 0.0

Current:

- 21 answers
- Aprio in 14 answers
- literal visibility 14/21 = 0.6666667

Current Aprio answer numbers:

```text
8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21
```

### Upstream metric mismatch

`companies_data` reported:

- count 10
- visibility 10/21 = 0.4761905

Therefore:

```text
literal count = 14
upstream count = 10
difference = 4
```

The product must emit `company_metric_mismatch`.

### Main Aprio article

URL:

```text
https://www.aprio.com/insights-events/when-does-cost-or-pricing-data-need-to-be-certified-and-what-does-that-mean/
```

Previous:

- raw occurrences: 24
- answer coverage: 17
- Aprio answer co-occurrence: 0
- cited without Aprio: 17

Current:

- raw occurrences: 87
- answer coverage: 21
- Aprio answer co-occurrence: 14
- cited without Aprio: 7

Important interpretation:

The article was already cited in 17 answers when Aprio was mentioned in zero answers. Its presence alone did not cause Aprio to appear.

In the current run, the article is cited in all 21 answers. The Aprio base rate is 14/21, and the URL co-occurrence rate is also 14/21. Therefore the simple association lift is approximately zero.

Classify it as a supporting/topic-context source unless page-diff or stronger evidence establishes more.

### New Aprio article

URL:

```text
https://www.aprio.com/insights-events/how-to-account-for-ai-and-token-costs-on-government-contracts-a-far-dfars-and-cas-compliance-guide-ins-article-gc/
```

Current:

- raw occurrences: 1
- answer coverage: 1
- answer number: 14

It may be a localized contributor to one answer. It cannot explain the broad 14-answer visibility gain.

### Recommendation pattern

Current answers 8–21 repeatedly include a similar group:

- Cherry Bekaert
- Aprio
- CohnReznick
- BDO
- Baker Tilly
- Bennett Thrasher

Current best interpretation:

> Gemini shifted toward repeatedly recommending a common group of GovCon CPA firms. Aprio benefited from this recommendation/source-pattern shift. Its main article was a supporting source, but no single new Aprio page explains the full gain.

Expected overall confidence:

- recommendation/source-pattern shift: medium
- single-page causation: low
