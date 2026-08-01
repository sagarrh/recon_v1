create table if not exists public.aivc_pipeline_runs (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null,
  canonical_name text not null,
  status text not null check (status in ('pending', 'running', 'partial', 'completed', 'failed')),
  requested_options jsonb not null default '{}'::jsonb,
  component_versions jsonb not null default '{}'::jsonb,
  started_at timestamptz,
  completed_at timestamptz,
  error_summary text,
  combined_bundle_id text,
  combined_bundle_checksum text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists aivc_pipeline_runs_client_created_idx
  on public.aivc_pipeline_runs(client_id, created_at desc);

create table if not exists public.aivc_pipeline_stages (
  id uuid primary key default gen_random_uuid(),
  parent_run_id uuid not null references public.aivc_pipeline_runs(id) on delete cascade,
  stage_name text not null,
  attempt integer not null default 1 check (attempt > 0),
  status text not null check (
    status in ('pending', 'running', 'completed', 'partial', 'failed', 'skipped')
  ),
  required boolean not null default true,
  child_run_id text,
  artifact_id text,
  input_checksum text,
  output_checksum text,
  lease_owner text,
  lease_expires_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  error_type text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(parent_run_id, stage_name, attempt)
);

create index if not exists aivc_pipeline_stages_status_idx
  on public.aivc_pipeline_stages(status, lease_expires_at);

create table if not exists public.aivc_signal_bundles (
  bundle_id text primary key,
  parent_run_id uuid references public.aivc_pipeline_runs(id) on delete set null,
  producer text not null,
  producer_run_id text not null,
  client_id uuid not null,
  schema_version text not null,
  analysis_start timestamptz,
  analysis_end timestamptz,
  status text not null check (status in ('complete', 'partial', 'failed')),
  payload jsonb not null,
  checksum text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(producer, client_id, producer_run_id, checksum)
);

create index if not exists aivc_signal_bundles_client_created_idx
  on public.aivc_signal_bundles(client_id, created_at desc);

create table if not exists public.aivc_delivery_log (
  id uuid primary key default gen_random_uuid(),
  parent_run_id uuid not null references public.aivc_pipeline_runs(id) on delete cascade,
  client_id uuid not null,
  channel text not null,
  destination_key text not null,
  artifact_key text not null,
  status text not null check (status in ('pending', 'delivered', 'failed', 'skipped')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  response_metadata jsonb not null default '{}'::jsonb,
  delivered_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(parent_run_id, channel, destination_key, artifact_key)
);

alter table public.aivc_pipeline_runs enable row level security;
alter table public.aivc_pipeline_stages enable row level security;
alter table public.aivc_signal_bundles enable row level security;
alter table public.aivc_delivery_log enable row level security;

create policy aivc_pipeline_runs_tenant_select
  on public.aivc_pipeline_runs for select
  using (client_id = public.ai_visibility_current_client_id());

create policy aivc_pipeline_stages_tenant_select
  on public.aivc_pipeline_stages for select
  using (
    exists (
      select 1 from public.aivc_pipeline_runs parent
      where parent.id = parent_run_id
        and parent.client_id = public.ai_visibility_current_client_id()
    )
  );

create policy aivc_signal_bundles_tenant_select
  on public.aivc_signal_bundles for select
  using (client_id = public.ai_visibility_current_client_id());

create policy aivc_delivery_log_tenant_select
  on public.aivc_delivery_log for select
  using (client_id = public.ai_visibility_current_client_id());
