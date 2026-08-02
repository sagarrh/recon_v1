# System Architecture

## High-level flow

```text
Company name
    |
    v
Client/company resolver
    |
    v
Historical run loader
    |
    v
Normalization layer
    |
    v
Provider/query time-series engine
    |
    v
Pairwise + rolling comparison engine
    |
    v
Signal candidate detector
    |
    v
Selective page retrieval
    |
    v
Page/company analysis + page diffs
    |
    v
Attribution/confidence engine
    |
    v
Company Intelligence Report
```

## Main modules

### 1. Company resolver

Input:

```json
{ "company_name": "Aprio" }
```

Responsibilities:

- resolve canonical client company
- resolve `client_id`
- load official domains
- load aliases
- load configured competitors
- reject ambiguous or non-client input

### 2. Run ingestion and normalization

Responsibilities:

- detect unprocessed raw rows
- validate JSON shapes
- resolve stable monitored-query identity
- reject invalid runs
- normalize answers
- recompute literal company mentions
- preserve upstream metrics
- normalize citations
- deduplicate answer/URL pairs
- flag bad citation positions

### 3. Historical analysis engine

Responsibilities:

- build provider/query time series
- compare adjacent valid runs
- compare latest run to rolling baseline
- detect trends
- detect change points
- detect provider divergence
- detect query and cluster concentration

### 4. Signal candidate detector

Responsibilities:

- identify material company changes
- identify competitor gains/losses
- identify source gains/losses
- identify recommendation-pattern shifts
- emit observation-only candidate records

### 5. Page intelligence pipeline

Responsibilities:

- prioritize URLs
- retrieve pages safely
- extract HTML/PDF text
- create snapshots and hashes
- detect company/competitor mentions
- classify mention role
- compare page versions

### 6. Attribution engine

Responsibilities:

- combine observation and page evidence
- score candidate explanations
- choose primary hypothesis
- list alternatives
- assign confidence
- prevent unsupported causal language

### 7. Report generator

Responsibilities:

- aggregate all history
- summarize company trend
- summarize provider/query/cluster performance
- summarize competitors
- construct change timeline
- rank signals
- generate actions
- expose evidence and warnings

### 8. API and UI

Minimum API:

- create or refresh report by company name
- retrieve report
- list report signals
- view signal evidence
- reprocess failed stages

Minimum UI:

- company input
- report summary
- trend sections
- provider/query drill-down
- competitor section
- source evidence
- actions and warnings

## Execution model

Use idempotent jobs.

Recommended fallback when the repository has no worker framework:

- PostgreSQL job table
- server-side worker
- `FOR UPDATE SKIP LOCKED`
- unique idempotency keys
- bounded retries
- dead-letter/failed status

## LLM boundary

LLMs may:

- classify nuanced mention roles
- summarize page changes
- create readable report narratives

LLMs must not:

- calculate visibility
- decide URL status from raw arrays
- select baselines
- determine answer coverage
- silently resolve metric mismatches
- invent historical page versions
- claim causation without structured evidence

All facts must be computed first and passed as structured evidence.
