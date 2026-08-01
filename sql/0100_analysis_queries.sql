-- 1. Historical runs for one client.
with params as (
  select
    'b88e87f3-0aa5-4da9-be48-2807b12d5a91'::uuid as client_id,
    now() - interval '90 days' as from_timestamp
)
select
  am.id as run_id,
  am.created_at,
  am.cluster_id,
  am.cluster_name,
  am.request_payload::jsonb ->> 'service' as service,
  am.request_payload::jsonb ->> 'method' as method,
  am.request_payload::jsonb ->> 'base_query' as base_query,
  am.request_payload::jsonb #>> '{user_data,task_id}' as task_id,
  jsonb_array_length(coalesce(am.answers_list::jsonb, '[]'::jsonb)) as answer_count,
  jsonb_array_length(coalesce(am.citations_list::jsonb, '[]'::jsonb)) as citation_group_count,
  (
    select count(*)
    from jsonb_object_keys(coalesce(am.citations_data::jsonb, '{}'::jsonb))
  ) as cited_domain_count,
  (
    select count(*)
    from jsonb_object_keys(coalesce(am.companies_data::jsonb, '{}'::jsonb))
  ) as tracked_company_count
from public.ai_monitoring am
join params p on am.client_id = p.client_id
where am.created_at >= p.from_timestamp
order by am.created_at;

-- 2. Validate literal Aprio count versus upstream metric.
with params as (
  select
    '1ca57705-51b6-47a6-9725-8d43dbb0a937'::uuid as current_run_id,
    'fb279a6c-f285-4c8a-afce-78469c9455e1'::uuid as previous_run_id,
    'Aprio'::text as company_name
),
runs as (
  select
    am.id as run_id,
    am.created_at,
    am.answers_list::jsonb as answers_list,
    am.companies_data::jsonb as companies_data,
    case
      when am.id = p.current_run_id then 'current'
      when am.id = p.previous_run_id then 'previous'
    end as run_type
  from public.ai_monitoring am
  cross join params p
  where am.id in (p.current_run_id, p.previous_run_id)
),
answer_stats as (
  select
    r.run_type,
    r.run_id,
    r.created_at,
    count(*) as total_answers,
    count(*) filter (
      where position(lower(p.company_name) in lower(answer.answer_text)) > 0
    ) as literal_company_answer_count,
    array_agg(answer.ordinality::int order by answer.ordinality) filter (
      where position(lower(p.company_name) in lower(answer.answer_text)) > 0
    ) as literal_company_answer_numbers
  from runs r
  cross join params p
  cross join lateral jsonb_array_elements_text(
    coalesce(r.answers_list, '[]'::jsonb)
  ) with ordinality as answer(answer_text, ordinality)
  group by r.run_type, r.run_id, r.created_at
),
stored_metrics as (
  select
    r.run_type,
    nullif(company.metrics ->> 'count', '')::numeric as stored_company_count,
    nullif(company.metrics ->> 'visibility', '')::numeric as stored_visibility,
    nullif(company.metrics ->> 'total_count', '')::numeric as stored_total_count
  from runs r
  cross join params p
  left join lateral jsonb_each(coalesce(r.companies_data, '{}'::jsonb))
    as company(company_name, metrics)
    on lower(company.company_name) = lower(p.company_name)
)
select
  a.*,
  s.stored_company_count,
  s.stored_visibility,
  s.stored_total_count,
  a.literal_company_answer_count - coalesce(s.stored_company_count, 0) as count_difference
from answer_stats a
left join stored_metrics s on s.run_type = a.run_type
order by case when a.run_type = 'previous' then 1 else 2 end;

-- 3. Exact URL answer coverage and raw occurrence deltas.
with params as (
  select
    '1ca57705-51b6-47a6-9725-8d43dbb0a937'::uuid as current_run_id,
    'fb279a6c-f285-4c8a-afce-78469c9455e1'::uuid as previous_run_id
),
runs as (
  select
    am.id as run_id,
    case
      when am.id = p.current_run_id then 'current'
      when am.id = p.previous_run_id then 'previous'
    end as run_type,
    am.citations_list::jsonb as citations_list
  from public.ai_monitoring am
  cross join params p
  where am.id in (p.current_run_id, p.previous_run_id)
),
citation_groups as (
  select
    r.run_type,
    citation_group.ordinality::int as answer_number,
    citation_group.citations
  from runs r
  cross join lateral jsonb_array_elements(
    coalesce(r.citations_list, '[]'::jsonb)
  ) with ordinality as citation_group(citations, ordinality)
),
citation_occurrences as (
  select
    cg.run_type,
    cg.answer_number,
    citation.citation_data ->> 'url' as original_url,
    rtrim(
      regexp_replace(
        lower(trim(citation.citation_data ->> 'url')),
        '#.*$',
        ''
      ),
      '/'
    ) as normalized_url
  from citation_groups cg
  cross join lateral jsonb_array_elements(
    case when jsonb_typeof(cg.citations) = 'array'
      then cg.citations else '[]'::jsonb end
  ) as citation(citation_data)
  where citation.citation_data ->> 'url' is not null
),
url_stats as (
  select
    run_type,
    normalized_url,
    min(original_url) as example_url,
    count(*) as raw_occurrences,
    count(distinct answer_number) as answer_coverage
  from citation_occurrences
  group by run_type, normalized_url
)
select * from url_stats
order by normalized_url, run_type;

-- 4. URL/Aprio co-occurrence and association inputs.
-- Use the complete query from the implementation docs or normalize into tables,
-- then calculate company_cooccurrence, company_base_rate, and association_lift.

-- 5. Compare request payloads for run comparability.
select
  id as run_id,
  created_at,
  request_payload::jsonb ->> 'service' as service,
  request_payload::jsonb ->> 'method' as method,
  request_payload::jsonb ->> 'base_query' as base_query,
  jsonb_pretty(request_payload::jsonb) as complete_request_payload
from public.ai_monitoring
where id in (
  '1ca57705-51b6-47a6-9725-8d43dbb0a937'::uuid,
  'fb279a6c-f285-4c8a-afce-78469c9455e1'::uuid
)
order by created_at;
