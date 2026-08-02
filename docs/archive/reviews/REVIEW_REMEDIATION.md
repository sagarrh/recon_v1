# Code Review Remediation

## Completed hardening

- Added tenant-scoped row-level security policies for every analytical table in
  `migrations/0002_tenant_rls.sql`.
- Added signal lifecycle hardening in `migrations/0003_processing_hardening.sql`.
- Corrected provider and overall visibility aggregation so asynchronous query
  timelines maintain a latest state per monitored query.
- Canonicalized known company aliases before normalization and prevented
  overlapping aliases from inflating literal mention counts.
- Preserved ordered meaningful URL query parameters while continuing to remove
  known tracking parameters.
- Integrated available page/company snapshots and temporally aligned page diffs
  into attribution scoring and hypothesis selection.
- Pinned HTTP connections to the IP address validated by the SSRF guard,
  disabled environment proxies, revalidated redirects, and constrained the
  optional browser fallback to one pinned hostname.
- Added per-domain page-fetch throttling, conditional-header redirect safety,
  byte limits, and expired worker-lease recovery.
- Prevented unscoped normalization/backfills from guessing that the
  alphabetically first company is the client.
- Made page-queue failure non-fatal to report persistence and local JSON/Markdown
  output.
- Added database statement timeouts and replaced the source-table full count
  with a catalog estimate.
- Deactivated stale signals during report regeneration and removed stale
  normalized answer relationships during reprocessing.
- Strengthened Pydantic and JSON Schema validation for providers, queries, and
  signals.
- Added structured report/job lifecycle logs.

## Verification

The default test command enforces at least 70% coverage:

```bash
uv run pytest
```

Current results:

- 19 tests pass
- 71% total coverage
- Ruff lint passes
- Ruff format check passes
- strict mypy passes
- installed dependency compatibility passes

The live Supabase migration and Aprio report still require `DATABASE_URL`.

## Page-fetch reliability and evidence quality

- Added failover across every pre-validated public DNS address while preserving
  connection pinning and redirect revalidation.
- Added bounded robots-policy caching, temporary HTTP status retries with
  `Retry-After`, early and streamed size checks, conservative content sniffing,
  and PDF page/text limits.
- Added extraction-quality classification, normalized relative links, and an
  opt-in secure Playwright fallback for low-quality static HTML.
- Browser-derived snapshots no longer reuse static-document cache validators.
- Persisted fetch/extraction metadata inside snapshot structured data. Unusable
  extraction cannot support negative mention evidence or direct attribution.
- Added bounded cross-domain worker concurrency while preserving per-domain
  request spacing and database job leases.
