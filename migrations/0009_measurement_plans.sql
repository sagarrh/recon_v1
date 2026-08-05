-- 0009_measurement_plans.sql
-- Post-execution outcome measurement: what we intend to measure, what we observed, what we concluded.
--
-- Three tables, deliberately separate, because they answer three different questions and are written
-- at three different times:
--   plans      what will be measured, fixed BEFORE the follow-up window closes
--   snapshots  the raw aggregate observed in one window from one source
--   outcomes   the conclusion drawn from comparing two snapshots
--
-- The plan is written first on purpose. Choosing the window after seeing the result is how a
-- measurement system talks itself into a favourable answer; pre-registering it removes the option.

create table if not exists public.ai_visibility_measurement_plans (
  id uuid primary key default gen_random_uuid(),
  -- Stable across re-runs so re-planning updates rather than duplicates.
  idempotency_key text not null unique,
  -- Polymorphic: a recon recommendation today, a citation signal later. One engine, two subjects.
  subject_type text not null check (subject_type in ('recon_recommendation', 'citation_signal')),
  subject_id text not null,
  client_id uuid not null,
  -- Copied from the execution record, not referenced: what was measured must stay what was
  -- measured, even if the recommendation or its execution is edited afterwards.
  anchor_at timestamptz not null,
  baseline_start date not null,
  baseline_end date not null,
  follow_up_start date not null,
  follow_up_end date not null,
  window_days integer not null check (window_days > 0),
  target_pages jsonb not null default '[]'::jsonb,
  target_queries jsonb not null default '[]'::jsonb,
  -- Per-target mapping decisions (status, method, what was rejected and why).
  mapping_details jsonb not null default '{}'::jsonb,
  required_sources text[] not null default array['gsc'],
  status text not null default 'planned' check (
    status in ('planned', 'waiting_for_window', 'ready', 'measured', 'unmeasurable')
  ),
  policy_version text not null default 'measurement_v1',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists ai_visibility_measurement_plans_client_idx
  on public.ai_visibility_measurement_plans(client_id, status);

create table if not exists public.ai_visibility_measurement_snapshots (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references public.ai_visibility_measurement_plans(id) on delete cascade,
  source text not null check (source in ('gsc', 'ga4')),
  window_type text not null check (window_type in ('baseline', 'follow_up')),
  requested_start date not null,
  requested_end date not null,
  -- What the source could actually supply, which may be narrower than requested.
  effective_start date,
  effective_end date,
  fresh_through date,
  row_count integer not null default 0,
  metrics jsonb not null default '{}'::jsonb,
  source_status text not null check (
    source_status in ('available', 'not_connected', 'stale', 'unmapped', 'refused', 'empty')
  ),
  warnings jsonb not null default '[]'::jsonb,
  -- Identifies the exact aggregate. Re-capturing identical data is a no-op rather than a new row.
  payload_checksum text not null,
  captured_at timestamptz not null default now(),
  unique (plan_id, source, window_type)
);

create table if not exists public.ai_visibility_measurement_outcomes (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null unique
    references public.ai_visibility_measurement_plans(id) on delete cascade,
  classification text not null,
  confidence text not null check (confidence in ('high', 'medium', 'low', 'none')),
  gsc_deltas jsonb not null default '[]'::jsonb,
  ga4_deltas jsonb not null default '[]'::jsonb,
  evidence_summary jsonb not null default '{}'::jsonb,
  -- What stops this being a stronger claim: no control page, single window, and so on. Present on
  -- every outcome, because a result without its limits reads as more certain than it is.
  limitations jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  algorithm_version text not null default 'outcome_v1',
  evaluated_at timestamptz not null default now()
);

alter table public.ai_visibility_measurement_plans enable row level security;
alter table public.ai_visibility_measurement_snapshots enable row level security;
alter table public.ai_visibility_measurement_outcomes enable row level security;

create policy ai_visibility_measurement_plans_tenant_select
  on public.ai_visibility_measurement_plans for select
  using (client_id = public.ai_visibility_current_client_id());

create policy ai_visibility_measurement_snapshots_tenant_select
  on public.ai_visibility_measurement_snapshots for select
  using (
    exists (
      select 1 from public.ai_visibility_measurement_plans plan
      where plan.id = plan_id
        and plan.client_id = public.ai_visibility_current_client_id()
    )
  );

create policy ai_visibility_measurement_outcomes_tenant_select
  on public.ai_visibility_measurement_outcomes for select
  using (
    exists (
      select 1 from public.ai_visibility_measurement_plans plan
      where plan.id = plan_id
        and plan.client_id = public.ai_visibility_current_client_id()
    )
  );
