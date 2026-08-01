-- geo_drop_crm_vendor_tables.sql — Remove the vendor-CRM tables from the agentic DB.
-- crm_deals / crm_leads are Multiplier AI's OWN sales pipeline (no client_id). They were never synced by
-- scout-sync nor read by any Scout code, and must never feed client revenue attribution. Drop the empty,
-- unused mirror tables so they can't be mistaken for a client-revenue source. Client attribution stays on
-- ga4_metrics (Channel A) / selection_events (Channel C) / attribution_events (Channel B, per-client).
DROP TABLE IF EXISTS public.crm_deals;
DROP TABLE IF EXISTS public.crm_leads;

-- Reload the PostgREST schema cache so the dropped tables disappear from the Scout API immediately.
NOTIFY pgrst, 'reload schema';
