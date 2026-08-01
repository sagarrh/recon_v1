-- Reference analytical views.

create or replace view public.v_valid_monitor_runs as
select
  am.*,
  mrp.monitor_query_id,
  mrp.configuration_completeness
from public.ai_monitoring am
join public.monitor_run_processing mrp on mrp.run_id = am.id
where mrp.is_valid = true
  and coalesce(jsonb_array_length(am.answers_list::jsonb), 0) > 0;

create or replace view public.v_run_company_metric_mismatches as
select
  rcm.*,
  c.canonical_name
from public.run_company_metrics rcm
join public.companies c on c.id = rcm.company_id
where coalesce(rcm.metric_difference, 0) <> 0;
