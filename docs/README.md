# Documentation Index

## Use the project

- [Operations guide](guides/OPERATIONS_GUIDE.md) — setup, commands, report generation,
  page processing, and troubleshooting.
- [Architecture overview](architecture/ARCHITECTURE_OVERVIEW.md) — end-to-end system
  behavior and module responsibilities.
- [Simplified reporting architecture](architecture/SIMPLIFIED_REPORTING_ARCHITECTURE.md)
  — current two-source report data flow.

## Future work

- [GSC and GA4 measurement plan](plans/POST_REPORT_GSC_GA4_MEASUREMENT_PLAN.md) —
  explicitly out of the current implementation scope.

## Source-system references

- `reference/database/agentic_db_snapshot.sql` — supplied Agentic DB schema snapshot.
- `reference/database/recon_query_for_report_reference.sql` — original manual Recon report
  query; runtime code uses `src/aivc/resources/sql/recon_report.sql`.
- `reference/upstream/geo_edge_function.ts` — supplied upstream GEO edge function.
- `reference/report-samples/report_jaggaer.html` — supplied client-facing report example.

## Archive

`archive/` contains completed implementation plans, old specifications, reviews, incident
notes, migration history imported from Recon, and the executed AIVC cleanup audit. Archive
documents explain historical decisions but are not operating instructions.
