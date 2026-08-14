-- ReconV1 GSC/GA4 measurement foundation.
-- Source metric tables remain upstream-owned; this migration only adds indexes
-- and ReconV1-owned action/measurement storage.

create index if not exists gsc_query_page_metrics_site_date_idx
  on public.gsc_query_page_metrics(site_url, metric_date);

create index if not exists gsc_query_page_metrics_site_page_date_idx
  on public.gsc_query_page_metrics(site_url, page, metric_date);

create index if not exists ga4_metrics_property_date_idx
  on public.ga4_metrics(property_id, metric_date);

create index if not exists ga4_metrics_property_page_date_idx
  on public.ga4_metrics(property_id, landing_page, metric_date);

create index if not exists user_metrics_gsc_site_idx
  on public.user_metrics(gsc_site_url)
  where gsc_site_url is not null;

create index if not exists user_metrics_ga4_property_idx
  on public.user_metrics(ga4_property_id)
  where ga4_property_id is not null;

create table if not exists public.aivc_action_executions (
  id uuid primary key default gen_random_uuid(),
  subject_type text not null check (
    subject_type in ('recon_recommendation', 'citation_signal')
  ),
  subject_id text not null,
  client_id uuid not null,
  status text not null default 'proposed' check (
    status in ('proposed', 'accepted', 'executed', 'verified', 'abandoned')
  ),
  implemented_at timestamptz,
  implemented_by text,
  target_pages jsonb not null default '[]'::jsonb,
  target_queries jsonb not null default '[]'::jsonb,
  action_type text not null default 'other',
  implementation_notes text,
  evidence_urls jsonb not null default '[]'::jsonb,
  verification_status text not null default 'unverified' check (
    verification_status in ('unverified', 'snapshot_confirmed', 'manual_confirmed')
  ),
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists aivc_action_executions_implementation_uidx
  on public.aivc_action_executions(
    client_id, subject_type, subject_id, implemented_at
  )
  where implemented_at is not null;

create index if not exists aivc_action_executions_client_status_idx
  on public.aivc_action_executions(client_id, status, implemented_at desc);

create table if not exists public.ai_visibility_measurement_plans (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null unique,
  subject_type text not null check (
    subject_type in ('recon_recommendation', 'citation_signal')
  ),
  subject_id text not null,
  client_id uuid not null,
  anchor_at timestamptz not null,
  baseline_start date not null,
  baseline_end date not null,
  follow_up_start date not null,
  follow_up_end date not null,
  window_days integer not null check (window_days > 0),
  target_pages jsonb not null default '[]'::jsonb,
  target_queries jsonb not null default '[]'::jsonb,
  mapping_details jsonb not null default '{}'::jsonb,
  required_sources text[] not null default array['gsc']::text[],
  status text not null default 'planned' check (
    status in (
      'planned', 'waiting_for_window', 'ready', 'measured', 'unmeasurable'
    )
  ),
  policy_version text not null default 'measurement_v1',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (baseline_start <= baseline_end),
  check (follow_up_start <= follow_up_end),
  check (baseline_end < follow_up_start)
);

create index if not exists ai_visibility_measurement_plans_due_idx
  on public.ai_visibility_measurement_plans(status, follow_up_end);

create index if not exists ai_visibility_measurement_plans_client_idx
  on public.ai_visibility_measurement_plans(client_id, created_at desc);

create table if not exists public.ai_visibility_measurement_snapshots (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references public.ai_visibility_measurement_plans(id)
    on delete cascade,
  source text not null check (source in ('gsc', 'ga4')),
  window_type text not null check (window_type in ('baseline', 'follow_up')),
  requested_start date not null,
  requested_end date not null,
  effective_start date,
  effective_end date,
  fresh_through date,
  row_count integer not null default 0 check (row_count >= 0),
  metrics jsonb not null default '{}'::jsonb,
  source_status text not null check (
    source_status in (
      'available', 'not_connected', 'stale', 'unmapped', 'refused', 'empty'
    )
  ),
  warnings jsonb not null default '[]'::jsonb,
  payload_checksum text not null,
  captured_at timestamptz not null default now(),
  check (requested_start <= requested_end)
);

create unique index if not exists ai_visibility_measurement_snapshots_payload_uidx
  on public.ai_visibility_measurement_snapshots(
    plan_id, source, window_type, payload_checksum
  );

create index if not exists ai_visibility_measurement_snapshots_latest_idx
  on public.ai_visibility_measurement_snapshots(
    plan_id, source, window_type, captured_at desc
  );

create table if not exists public.ai_visibility_measurement_outcomes (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null unique references public.ai_visibility_measurement_plans(id)
    on delete cascade,
  classification text not null,
  confidence text not null check (
    confidence in ('high', 'medium', 'low', 'none')
  ),
  gsc_deltas jsonb not null default '[]'::jsonb,
  ga4_deltas jsonb not null default '[]'::jsonb,
  evidence_summary jsonb not null default '{}'::jsonb,
  limitations jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  algorithm_version text not null default 'outcome_v1',
  evaluated_at timestamptz not null default now()
);

alter table public.ai_visibility_measurement_outcomes
  add column if not exists revenue_category text not null default 'unavailable',
  add column if not exists recorded_revenue numeric,
  add column if not exists observed_revenue_delta numeric,
  add column if not exists currency_code text;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.ai_visibility_measurement_outcomes'::regclass
      and conname = 'ai_visibility_measurement_outcomes_revenue_category_check'
  ) then
    alter table public.ai_visibility_measurement_outcomes
      add constraint ai_visibility_measurement_outcomes_revenue_category_check
      check (revenue_category in ('recorded', 'unavailable'));
  end if;
end
$$;

alter table public.aivc_action_executions enable row level security;
alter table public.ai_visibility_measurement_plans enable row level security;
alter table public.ai_visibility_measurement_snapshots enable row level security;
alter table public.ai_visibility_measurement_outcomes enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'aivc_action_executions'
      and policyname = 'aivc_action_executions_tenant_select'
  ) then
    create policy aivc_action_executions_tenant_select
      on public.aivc_action_executions for select
      using (client_id = public.ai_visibility_current_client_id());
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'ai_visibility_measurement_plans'
      and policyname = 'ai_visibility_measurement_plans_tenant_select'
  ) then
    create policy ai_visibility_measurement_plans_tenant_select
      on public.ai_visibility_measurement_plans for select
      using (client_id = public.ai_visibility_current_client_id());
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'ai_visibility_measurement_snapshots'
      and policyname = 'ai_visibility_measurement_snapshots_tenant_select'
  ) then
    create policy ai_visibility_measurement_snapshots_tenant_select
      on public.ai_visibility_measurement_snapshots for select
      using (
        exists (
          select 1 from public.ai_visibility_measurement_plans plan
          where plan.id = plan_id
            and plan.client_id = public.ai_visibility_current_client_id()
        )
      );
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'ai_visibility_measurement_outcomes'
      and policyname = 'ai_visibility_measurement_outcomes_tenant_select'
  ) then
    create policy ai_visibility_measurement_outcomes_tenant_select
      on public.ai_visibility_measurement_outcomes for select
      using (
        exists (
          select 1 from public.ai_visibility_measurement_plans plan
          where plan.id = plan_id
            and plan.client_id = public.ai_visibility_current_client_id()
        )
      );
  end if;
end
$$;
