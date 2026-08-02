# Security, Observability, and Runbook

## Security

### Tenant isolation

All normalized data, signals, and reports must remain client-scoped.

### Worker credentials

Use Supabase service role only on trusted server-side workers.

### SSRF protection

The page fetcher must block:

- localhost
- private ranges
- link-local ranges
- cloud metadata endpoints
- internal DNS results

Revalidate after redirects.

### Content safety

- do not execute scripts
- do not trust MIME type alone
- limit content size
- sanitize HTML
- store text, not executable content
- isolate headless browser

## Job reliability

Every stage must be:

- idempotent
- resumable
- retryable
- observable

Use:

- unique idempotency key
- bounded attempts
- backoff
- `FOR UPDATE SKIP LOCKED`
- failed status
- manual reprocess

## Structured logs

Include:

- client ID
- company ID
- raw run ID
- monitor query ID
- comparison ID
- page ID
- signal ID
- report ID
- stage
- duration
- error category
- retry count

## Metrics

Track:

- raw runs discovered
- valid/invalid runs
- normalized answers
- metric mismatches
- comparisons
- signals
- page jobs
- page success/failure
- extraction failure
- snapshot changes
- confidence distribution
- report generation duration
- report failures

## Backfill

Chronological order:

1. resolve monitor queries
2. normalize runs
3. calculate metrics
4. build comparisons
5. build time series
6. create observation signals
7. queue prioritized current pages
8. mark historical page evidence unavailable
9. generate reports

## Operational commands

Codex should add repository-appropriate commands for:

- normalize one run
- normalize date range
- build one client history
- fetch one page
- reprocess one comparison
- regenerate one report
- backfill all
- list failed jobs
- retry failed jobs

## Failure behavior

The report should still render partial results when:

- some pages cannot be fetched
- historical snapshots are missing
- one provider has invalid runs
- upstream metrics mismatch
- citation positions are unavailable

Failures become warnings, not fabricated evidence.
