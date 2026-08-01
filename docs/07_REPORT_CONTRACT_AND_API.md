# Report Contract and API

## User-facing input

Only:

```json
{
  "company_name": "Aprio"
}
```

Optional later fields:

- analysis_start
- analysis_end
- force_refresh

Default period:

- full valid history

## Recommended API

### Start or refresh report

```http
POST /api/company-intelligence-reports
```

```json
{
  "company_name": "Aprio"
}
```

Possible response:

```json
{
  "report_id": "uuid",
  "company_name": "Aprio",
  "status": "processing"
}
```

### Retrieve report

```http
GET /api/company-intelligence-reports/{report_id}
```

### Retrieve latest report by client company

```http
GET /api/company-intelligence-reports/latest?company_name=Aprio
```

### Reprocess report

Admin-only:

```http
POST /api/company-intelligence-reports/{report_id}/reprocess
```

## Report top-level sections

```json
{
  "report_type": "company_intelligence_report",
  "company": {},
  "analysis_period": {},
  "executive_summary": {},
  "overall_visibility": {},
  "provider_intelligence": [],
  "query_intelligence": [],
  "cluster_intelligence": [],
  "mention_quality": {},
  "citation_intelligence": {},
  "page_intelligence": {},
  "competitor_intelligence": [],
  "change_timeline": [],
  "signals": [],
  "recommended_actions": [],
  "data_quality_flags": [],
  "methodology": {}
}
```

## Required report details

### Company

- canonical name
- client ID
- official domains
- aliases
- competitor count

### Analysis period

- first valid run
- latest valid run
- valid run count
- invalid run count
- provider count
- query count
- cluster count

### Executive summary

- overall direction
- latest state
- largest positive change
- largest negative change
- strongest provider
- weakest provider
- major competitor movement
- primary hypothesis
- confidence
- top actions

### Provider intelligence

For each provider:

- run count
- latest visibility
- average visibility
- trend
- volatility
- change points
- top queries
- weak queries
- source shifts
- competitor movements

### Query intelligence

For each query:

- provider
- cluster
- run history
- current visibility
- rolling baseline
- trend
- latest delta
- company mention quality
- competitors
- source changes
- signals

### Citation intelligence

- new URLs
- removed URLs
- top answer-coverage gains
- top answer-coverage losses
- owned-source trend
- competitor-owned trend
- third-party trend
- regulatory sources
- raw occurrence warnings

### Page intelligence

- page mentions company
- mention role
- prominence
- official link
- competitor mentions
- content changes
- snapshot availability
- attribution relationship

### Competitor intelligence

- visibility timeline
- provider/query gains
- provider/query losses
- displacement events
- successful owned pages
- successful third-party pages
- recommendation bundle overlap

### Change timeline

Chronological material events across all runs.

### Signals

Each signal includes:

- observed change
- primary hypothesis
- confidence
- evidence
- alternatives
- actions
- warnings

## Machine-readable schema

See:

- `schemas/company_intelligence_report.schema.json`
- `schemas/api.openapi.yaml`
