create table if not exists public.aivc_final_reports (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null unique,
  parent_run_id uuid not null references public.aivc_pipeline_runs(id) on delete cascade,
  client_id uuid not null,
  report_profile text not null check (report_profile in ('decision', 'detailed')),
  schema_version text not null,
  config_version text not null,
  report_config_hash text not null,
  input_checksum text not null,
  source_bundle_ids jsonb not null,
  source_bundle_checksums jsonb not null,
  status text not null check (status in ('complete', 'partial', 'blocked', 'failed')),
  structured_snapshot jsonb,
  artifact_manifest jsonb not null default '{}'::jsonb,
  data_quality_flags jsonb not null default '[]'::jsonb,
  generated_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists aivc_final_reports_client_generated_idx
  on public.aivc_final_reports(client_id, generated_at desc);

create index if not exists aivc_final_reports_parent_profile_idx
  on public.aivc_final_reports(parent_run_id, report_profile);

alter table public.aivc_final_reports enable row level security;

create policy aivc_final_reports_tenant_select
  on public.aivc_final_reports for select
  using (client_id = public.ai_visibility_current_client_id());
