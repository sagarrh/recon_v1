# Data Model and Supabase Design

## Raw evidence

Keep `public.ai_monitoring` immutable.

## Proposed normalized tables

### `monitor_queries`

Stable identity for one monitored query/provider/configuration.

Key columns:

- id
- client_id
- cluster_id
- normalized_base_query
- base_query
- service
- method
- configuration_hash
- configuration_completeness
- timestamps

### `monitor_run_processing`

Tracks normalization and pipeline status for each raw run.

### `monitor_answers`

One row per answer.

Unique:

```text
run_id + answer_number
```

### `companies`

Canonical company.

### `company_aliases`

Aliases used for literal matching and normalization.

### `client_companies`

Maps client company and competitors to a client.

Relationships:

- client
- competitor
- partner
- other_tracked

### `answer_company_mentions`

One company’s appearances in one answer.

Store:

- literal count
- first position
- snippets
- prominence
- mention role
- detection method
- confidence

### `run_company_metrics`

Store both:

- recomputed literal metrics
- upstream `companies_data` metrics
- mismatch flags

### `citation_pages`

One normalized page.

### `answer_citations`

One deduplicated answer/page relationship plus raw occurrence count.

### `run_comparisons`

Previous/current or rolling-baseline comparison.

### `comparison_company_deltas`

Company changes for a comparison.

### `comparison_url_deltas`

Exact URL coverage changes.

### `page_fetch_jobs`

Prioritized retrieval queue.

### `page_snapshots`

Retrieved page versions.

### `page_company_mentions`

Company relationship to one page snapshot.

### `page_snapshot_diffs`

Changes between page versions.

### `signals`

Final structured signal.

### `signal_evidence`

Transparent evidence components.

### `company_intelligence_reports`

Persisted report snapshots.

Suggested columns:

- id
- client_id
- company_id
- analysis_start
- analysis_end
- report_version
- status
- structured_report
- generated_at
- last_error

## Required indexes

At minimum:

- raw monitoring: client_id, created_at
- monitor_queries: client_id, service, method
- monitor_answers: run_id, answer_number
- run_company_metrics: run_id, company_id
- answer_company_mentions: answer_id, company_id
- answer_citations: answer_id, page_id
- comparisons: monitor_query_id, current_run_id
- page jobs: status, run_after, priority
- snapshots: page_id, retrieved_at
- signals: client_id, company_id, created_at, signal_type
- reports: client_id, company_id, generated_at

## RLS

Application users must only access rows belonging to their client.

Workers use service-role credentials server-side only.

Do not expose:

- page bodies across clients
- internal fetch errors to ordinary users
- other tenants’ competitors or reports
- service-role credentials in client code

## Configuration hash

The hash should include all available execution controls:

- service
- method
- model
- model version
- prompt version
- pipeline version
- retrieval configuration
- language
- geography
- generation settings

The current raw payload may not contain all fields. Record configuration completeness and lower comparison confidence when incomplete.

## Migration reference

See:

- `sql/0001_reference_schema.sql`
- `sql/0002_reference_views.sql`

Codex should adapt these to repository conventions instead of blindly applying them.
