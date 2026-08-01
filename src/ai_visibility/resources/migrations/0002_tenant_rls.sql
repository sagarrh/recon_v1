-- Tenant isolation for every public analytical table.
-- Trusted migration/worker roles may bypass RLS; ordinary sessions must set:
--   select set_config('app.client_id', '<client uuid>', true);

create or replace function public.ai_visibility_current_client_id()
returns uuid
language sql
stable
as $$
  select case
    when current_user in ('anon', 'authenticated') then
      coalesce(
        nullif(current_setting('request.jwt.claim.client_id', true), ''),
        nullif(current_setting('request.jwt.claims', true), '')::jsonb
          #>> '{app_metadata,client_id}',
        nullif(current_setting('request.jwt.claims', true), '')::jsonb
          ->> 'client_id'
      )::uuid
    else nullif(current_setting('app.client_id', true), '')::uuid
  end
$$;

create or replace function public.ai_visibility_can_access_company(target_company_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1
    from public.ai_visibility_client_companies mapping
    where mapping.company_id = target_company_id
      and mapping.client_id = public.ai_visibility_current_client_id()
  )
$$;

create or replace function public.ai_visibility_can_access_run(target_run_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1
    from public.ai_visibility_run_processing processing
    where processing.run_id = target_run_id
      and processing.client_id = public.ai_visibility_current_client_id()
  )
$$;

create or replace function public.ai_visibility_can_access_answer(target_answer_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1
    from public.ai_visibility_answers answer
    join public.ai_visibility_run_processing processing
      on processing.run_id = answer.run_id
    where answer.id = target_answer_id
      and processing.client_id = public.ai_visibility_current_client_id()
  )
$$;

create or replace function public.ai_visibility_can_access_page(target_page_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1
    from public.ai_visibility_answer_citations citation
    join public.ai_visibility_answers answer on answer.id = citation.answer_id
    join public.ai_visibility_run_processing processing
      on processing.run_id = answer.run_id
    where citation.page_id = target_page_id
      and processing.client_id = public.ai_visibility_current_client_id()
  )
  or exists (
    select 1
    from public.ai_visibility_page_fetch_jobs job
    where job.page_id = target_page_id
      and job.client_id = public.ai_visibility_current_client_id()
  )
$$;

alter table public.ai_visibility_companies enable row level security;
alter table public.ai_visibility_company_aliases enable row level security;
alter table public.ai_visibility_client_companies enable row level security;
alter table public.ai_visibility_monitor_queries enable row level security;
alter table public.ai_visibility_run_processing enable row level security;
alter table public.ai_visibility_answers enable row level security;
alter table public.ai_visibility_run_company_metrics enable row level security;
alter table public.ai_visibility_answer_company_mentions enable row level security;
alter table public.ai_visibility_citation_pages enable row level security;
alter table public.ai_visibility_answer_citations enable row level security;
alter table public.ai_visibility_run_comparisons enable row level security;
alter table public.ai_visibility_page_fetch_jobs enable row level security;
alter table public.ai_visibility_page_snapshots enable row level security;
alter table public.ai_visibility_page_company_mentions enable row level security;
alter table public.ai_visibility_page_snapshot_diffs enable row level security;
alter table public.ai_visibility_signals enable row level security;
alter table public.ai_visibility_reports enable row level security;

create policy ai_visibility_companies_tenant_select
  on public.ai_visibility_companies for select
  using (public.ai_visibility_can_access_company(id));

create policy ai_visibility_aliases_tenant_select
  on public.ai_visibility_company_aliases for select
  using (public.ai_visibility_can_access_company(company_id));

create policy ai_visibility_client_companies_tenant_select
  on public.ai_visibility_client_companies for select
  using (client_id = public.ai_visibility_current_client_id());

create policy ai_visibility_queries_tenant_select
  on public.ai_visibility_monitor_queries for select
  using (client_id = public.ai_visibility_current_client_id());

create policy ai_visibility_runs_tenant_select
  on public.ai_visibility_run_processing for select
  using (client_id = public.ai_visibility_current_client_id());

create policy ai_visibility_answers_tenant_select
  on public.ai_visibility_answers for select
  using (public.ai_visibility_can_access_run(run_id));

create policy ai_visibility_metrics_tenant_select
  on public.ai_visibility_run_company_metrics for select
  using (public.ai_visibility_can_access_run(run_id));

create policy ai_visibility_answer_mentions_tenant_select
  on public.ai_visibility_answer_company_mentions for select
  using (public.ai_visibility_can_access_answer(answer_id));

create policy ai_visibility_pages_tenant_select
  on public.ai_visibility_citation_pages for select
  using (public.ai_visibility_can_access_page(id));

create policy ai_visibility_citations_tenant_select
  on public.ai_visibility_answer_citations for select
  using (public.ai_visibility_can_access_answer(answer_id));

create policy ai_visibility_comparisons_tenant_select
  on public.ai_visibility_run_comparisons for select
  using (
    exists (
      select 1
      from public.ai_visibility_monitor_queries query
      where query.id = monitor_query_id
        and query.client_id = public.ai_visibility_current_client_id()
    )
  );

create policy ai_visibility_page_jobs_tenant_select
  on public.ai_visibility_page_fetch_jobs for select
  using (client_id = public.ai_visibility_current_client_id());

create policy ai_visibility_snapshots_tenant_select
  on public.ai_visibility_page_snapshots for select
  using (public.ai_visibility_can_access_page(page_id));

create policy ai_visibility_page_mentions_tenant_select
  on public.ai_visibility_page_company_mentions for select
  using (
    exists (
      select 1
      from public.ai_visibility_page_snapshots snapshot
      where snapshot.id = snapshot_id
        and public.ai_visibility_can_access_page(snapshot.page_id)
    )
  );

create policy ai_visibility_page_diffs_tenant_select
  on public.ai_visibility_page_snapshot_diffs for select
  using (public.ai_visibility_can_access_page(page_id));

create policy ai_visibility_signals_tenant_select
  on public.ai_visibility_signals for select
  using (client_id = public.ai_visibility_current_client_id());

create policy ai_visibility_reports_tenant_select
  on public.ai_visibility_reports for select
  using (client_id = public.ai_visibility_current_client_id());

revoke all on table public.ai_visibility_schema_migrations from anon, authenticated;
