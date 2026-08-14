# Simplified Reporting Architecture

## Product decision

The backend produces one compact, checksummed evidence input containing AI
Citation and Recon facts. Codex or Claude Code creates the final client-facing
HTML manually from that input and the reusable report prompt.

## Data flow

```text
public.ai_monitoring ──> Citation normalization ──> compact Citation input ──┐
                                                                            │
Recon run ──> Agentic DB ──> read-only Recon SQL ──> compact Recon input ───┤
                                                                            │
                                                     combined input JSON <──┘
                                                            │
                                                            v
                                              Codex/Claude + reusable prompt
                                                            │
                                                            v
                                                   standalone client HTML
```

A fresh cycle runs and persists Citation and Recon before executing the
read-only Recon query. Historical input preparation reuses one exact parent and
does not rerun either producer.

## Storage levels

1. **Ledger:** complete Citation and Recon database records for audit, debugging,
   page history, and future reconstruction.
2. **Source bundles:** one Citation bundle and one Recon bundle per parent run.
3. **Report inputs:** compact Citation JSON, compact Recon JSON, and the combined
   `report-input-snapshot.json`, all checksummed and written atomically.
4. **Presentation:** a manually generated standalone HTML file. It is not written
   to the database by the application.

## Database disposition

| Group | Current disposition | Reason |
|---|---|---|
| `public.ai_monitoring` | Keep, immutable | Authoritative Citation ledger |
| Recon source/write tables | Keep | Recon populates them before reporting SQL runs |
| `ai_visibility_*` tables | Keep | Metrics, provenance, jobs, and page intelligence |
| `aivc_pipeline_runs`, `aivc_pipeline_stages` | Keep | Run ordering and auditability |
| `aivc_signal_bundles` | Keep two bundles per parent | Exact historical reconstruction |
| `aivc_final_reports` | Legacy removal target | No current runtime writes; removed only by the separately approved cleanup migration |

## LLM boundary

Recon may use its configured models during investigation and recommendation
generation. The final report-input stage makes no additional LLM call. The
manual report generator receives only `report-input-snapshot.json` and the
reusable prompt at `output/docs/prompts/CLIENT_REPORT_GENERATION_PROMPT.md`.
