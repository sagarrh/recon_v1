# ReconV1 GSC and GA4 Measurement

ReconV1 uses the GSC and GA4 metrics already synchronized from the upstream
GEO platform. It does not connect to Google or store Google refresh tokens.

The measurement workflow begins only after a recommendation or citation action
has actually been implemented and verified:

```text
GEO sync -> readiness check -> verified action -> fixed windows
         -> source snapshots -> observed outcome
```

## 1. Apply the measurement migration

```powershell
uv run ai-visibility db migrate
```

Migration `0010_gsc_ga4_measurement_foundation.sql` adds source-query indexes,
idempotency protection, outcome revenue fields, and tenant read policies. It
does not modify `public.ai_monitoring` or write source GSC/GA4 metric rows.

## 2. Validate one client

```powershell
uv run aivc measurement check --company "Aprio"
```

Use `--client-id` when a company name is duplicated. The command is read-only
and checks:

- GSC and GA4 client handles without printing their values;
- latest mirrored dates and sync status;
- shared handles that could cross tenant boundaries;
- duplicate metric keys that could double-count outcomes;
- client currency and recorded-revenue availability;
- RLS isolation for source and measurement tables.

A client must pass the GSC readiness gate before a plan can be created. GA4 may
remain optional unless `--require-ga4` is used.

## 3. Start measuring an implemented action

Use a persisted ReconV1 recommendation or citation-signal UUID. The timestamp
must include a timezone offset.

```powershell
uv run aivc measurement start `
  --client-id "CLIENT-UUID" `
  --subject-type recon_recommendation `
  --subject-id "RECOMMENDATION-UUID" `
  --implemented-at "2026-08-14T10:00:00+05:30" `
  --implemented-by "customer-success@example.com" `
  --verification-status manual_confirmed `
  --action-type content_update `
  --target-page "https://example.com/exact-page" `
  --target-query "exact target query" `
  --evidence-url "https://example.com/exact-page"
```

By default, ReconV1 records 28 days before implementation, waits seven days,
and then measures a 28-day follow-up. Repeating the same command returns the
same action and plan instead of creating duplicates.

GA4 is never evaluated property-wide. It requires an exact target page. GSC
uses the exact intersection of supplied pages and queries; a query-only plan is
allowed but receives lower confidence.

## 4. Capture and evaluate

This command is safe to schedule daily:

```powershell
uv run aivc measurement run --plan-id "PLAN-UUID"
```

It captures any window whose end date plus source-data lag has passed. Until
the follow-up is available, the plan remains `waiting_for_window`. When all
required evidence is ready, the same command evaluates and persists the
outcome.

For controlled historical verification only:

```powershell
uv run aivc measurement run --plan-id "PLAN-UUID" --as-of 2026-12-01
```

Inspect the compact result at any time:

```powershell
uv run aivc measurement status --plan-id "PLAN-UUID"
```

## 5. Calculation rules

- GSC clicks and impressions are summed.
- GSC CTR is recalculated as total clicks divided by total impressions.
- GSC position is weighted by impressions.
- GA4 sessions, engaged sessions and conversions are summed only for exact
  target landing pages.
- GA4 unique-user fields are excluded because they are not safely additive.
- Duplicate source keys are refused rather than double-counted.
- Shared client handles are refused rather than risking cross-client data.

`recorded` revenue means GA4 reported that revenue on the measured target page.
It does not mean ReconV1 caused the revenue. Every outcome retains the explicit
limitation `observational_before_after_comparison_not_causal`.

CRM attribution and incremental causal estimates are intentionally outside this
foundation. They should be added only after the GSC/GA4 measurement lifecycle
has been validated with real client actions.
