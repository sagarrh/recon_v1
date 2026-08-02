# Simplified Reporting Architecture

## Product decision

The system publishes one report product: a detailed, client-facing AI
Visibility and Competitive Intelligence Report. Decision/internal variants are
not part of the public CLI.

## Data flow

```text
public.ai_monitoring ──> Citation normalization ──> compact Citation input ──┐
                                                                            │
Recon run ──> Agentic DB ──> read-only Recon SQL ──> compact Recon input ───┤
                                                                            │
                                                validated prompt envelope ──┘
                                                            │
                                                            v
                                                structured LLM narrative
                                                            │
                                                            v
                                             deterministic HTML/MD renderer
```

A fresh report must run Recon and persist its output before the Recon SQL is
executed. A historical rerender may reuse a completed evidence parent and does
not rerun either producer.

## Storage levels

1. **Ledger:** complete Citation and Recon database records used for audit,
   debugging, and future reconstruction. The ledger is not sent wholesale to
   the narrative model.
2. **Report inputs:** compact Citation JSON, compact Recon JSON, and the exact
   combined prompt envelope. These are schema-validated and checksummed.
3. **Report content:** structured narrative JSON returned by the LLM. Metrics
   and recommended actions remain deterministic.
4. **Presentation:** Markdown and HTML rendered by application templates.

## Database disposition

| Group | Current disposition | Reason |
|---|---|---|
| `public.ai_monitoring` | Keep, immutable | Authoritative Citation ledger |
| Recon source/write tables | Keep | Recon must populate them before reporting SQL runs |
| `ai_visibility_*` normalization and page tables | Keep | Correct metrics, evidence provenance, and safe page intelligence |
| `aivc_pipeline_runs`, `aivc_pipeline_stages` | Keep | Run ordering, failure recovery, and auditability |
| `aivc_final_reports` | Keep | One durable final report snapshot and artifact manifest |
| `aivc_signal_bundles` | Keep two source bundles per parent | Historical resolution depends on the Citation and Scout checksums; combined copies were removed |
| `aivc_delivery_log` | Proposed drop | Empty and has no runtime code references |

The combined signal bundle and legacy profile/audience runtime branches have
been removed. See `DATABASE_AND_CODE_CLEANUP_AUDIT.md` for the reviewed,
explicitly gated database cleanup.

## LLM boundary

The LLM receives the compact prompt envelope and writes prose fields only. It
cannot change measured metrics, SOV positions, evidence identifiers, or
recommended actions. Its response is validated against a strict Pydantic
schema. Unsupported numeric claims cause a deterministic fallback unless the
operator has configured LLM failure to be fatal.

The system prompt is versioned at
`src/aivc/resources/prompts/client_report_system.md`; its SHA-256 checksum is
stored in every prompt envelope.
