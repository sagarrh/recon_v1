-- Append-only analytical schema. public.ai_monitoring remains immutable.

create table if not exists public.ai_visibility_schema_migrations (
  version text primary key,
  checksum text not null,
  applied_at timestamptz not null default now()
);

create table if not exists public.ai_visibility_companies (
  id uuid primary key default gen_random_uuid(),
  canonical_name text not null,
  normalized_name text not null unique,
  official_domains jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.ai_visibility_company_aliases (
  company_id uuid not null references public.ai_visibility_companies(id) on delete cascade,
  alias text not null,
  normalized_alias text not null,
  is_primary boolean not null default false,
  created_at timestamptz not null default now(),
  primary key (company_id, normalized_alias)
);

create table if not exists public.ai_visibility_client_companies (
  client_id uuid not null,
  company_id uuid not null references public.ai_visibility_companies(id) on delete cascade,
  relationship text not null check (relationship in ('client', 'competitor', 'other_tracked')),
  priority integer not null default 0,
  created_at timestamptz not null default now(),
  primary key (client_id, company_id)
);

create table if not exists public.ai_visibility_monitor_queries (
  id uuid primary key default gen_random_uuid(),
  query_key text not null unique,
  client_id uuid not null,
  cluster_id text,
  cluster_name text,
  base_query text not null,
  normalized_base_query text not null,
  service text not null,
  method text not null,
  configuration_hash text not null,
  configuration_completeness text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.ai_visibility_run_processing (
  run_id uuid primary key,
  client_id uuid not null,
  monitor_query_id uuid not null references public.ai_visibility_monitor_queries(id),
  source_created_at timestamptz not null,
  status text not null,
  is_valid boolean not null,
  invalid_reason text,
  pipeline_version text not null,
  data_quality_flags jsonb not null default '[]'::jsonb,
  normalized_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.ai_visibility_answers (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.ai_visibility_run_processing(run_id) on delete cascade,
  answer_number integer not null,
  answer_text text not null,
  answer_hash text not null,
  word_count integer not null,
  created_at timestamptz not null default now(),
  unique (run_id, answer_number)
);

create table if not exists public.ai_visibility_run_company_metrics (
  run_id uuid not null references public.ai_visibility_run_processing(run_id) on delete cascade,
  company_id uuid not null references public.ai_visibility_companies(id),
  literal_answer_count integer not null,
  literal_answer_numbers jsonb not null,
  literal_visibility double precision not null,
  literal_total_mentions integer not null,
  upstream_count double precision,
  upstream_visibility double precision,
  upstream_word_count double precision,
  metric_difference double precision,
  data_quality_flags jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (run_id, company_id)
);

create table if not exists public.ai_visibility_answer_company_mentions (
  answer_id uuid not null references public.ai_visibility_answers(id) on delete cascade,
  company_id uuid not null references public.ai_visibility_companies(id) on delete cascade,
  literal_mention_count integer not null,
  first_position integer,
  context_snippets jsonb not null default '[]'::jsonb,
  detection_method text not null default 'boundary_literal_alias',
  confidence double precision not null default 1.0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (answer_id, company_id, detection_method)
);

create table if not exists public.ai_visibility_citation_pages (
  id uuid primary key default gen_random_uuid(),
  normalized_url text not null unique,
  domain text not null,
  publisher_company_id uuid references public.ai_visibility_companies(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.ai_visibility_answer_citations (
  answer_id uuid not null references public.ai_visibility_answers(id) on delete cascade,
  page_id uuid not null references public.ai_visibility_citation_pages(id) on delete cascade,
  original_url text not null,
  title text,
  raw_occurrence_count integer not null,
  start_index integer,
  end_index integer,
  position_quality text not null,
  created_at timestamptz not null default now(),
  primary key (answer_id, page_id)
);

create table if not exists public.ai_visibility_run_comparisons (
  id uuid primary key default gen_random_uuid(),
  monitor_query_id uuid not null references public.ai_visibility_monitor_queries(id),
  previous_run_id uuid not null,
  current_run_id uuid not null,
  comparison_type text not null,
  comparability_score double precision not null,
  comparability_flags jsonb not null default '[]'::jsonb,
  structured_delta jsonb not null,
  created_at timestamptz not null default now(),
  unique (previous_run_id, current_run_id, comparison_type)
);

create table if not exists public.ai_visibility_page_fetch_jobs (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references public.ai_visibility_citation_pages(id) on delete cascade,
  client_id uuid not null,
  priority integer not null default 0,
  reason text not null,
  status text not null default 'pending',
  attempts integer not null default 0,
  run_after timestamptz not null default now(),
  locked_at timestamptz,
  locked_by text,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (page_id, client_id, reason)
);

create table if not exists public.ai_visibility_page_snapshots (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references public.ai_visibility_citation_pages(id) on delete cascade,
  retrieved_at timestamptz not null default now(),
  http_status integer,
  final_url text,
  content_type text,
  title text,
  main_text text,
  main_text_hash text,
  raw_content_hash text,
  etag text,
  last_modified_header text,
  structured_data jsonb not null default '{}'::jsonb,
  fetch_method text,
  fetch_error text,
  robots_allowed boolean,
  created_at timestamptz not null default now(),
  unique (page_id, raw_content_hash)
);

create table if not exists public.ai_visibility_page_company_mentions (
  snapshot_id uuid not null references public.ai_visibility_page_snapshots(id) on delete cascade,
  company_id uuid not null references public.ai_visibility_companies(id),
  mention_count integer not null,
  mentioned_in_title boolean not null default false,
  mentioned_in_heading boolean not null default false,
  links_to_official_domain boolean not null default false,
  publisher_is_company boolean not null default false,
  mention_role text not null,
  context_snippets jsonb not null default '[]'::jsonb,
  confidence double precision not null default 1.0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (snapshot_id, company_id)
);

create table if not exists public.ai_visibility_page_snapshot_diffs (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references public.ai_visibility_citation_pages(id) on delete cascade,
  previous_snapshot_id uuid not null references public.ai_visibility_page_snapshots(id),
  current_snapshot_id uuid not null references public.ai_visibility_page_snapshots(id),
  text_similarity double precision,
  material_change boolean not null,
  change_summary jsonb not null,
  created_at timestamptz not null default now(),
  unique (previous_snapshot_id, current_snapshot_id)
);

create table if not exists public.ai_visibility_signals (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null unique,
  client_id uuid not null,
  company_id uuid references public.ai_visibility_companies(id),
  signal_type text not null,
  severity double precision not null,
  confidence text not null,
  structured_payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.ai_visibility_reports (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null unique,
  client_id uuid not null,
  company_id uuid not null references public.ai_visibility_companies(id),
  company_name text not null,
  analysis_start timestamptz,
  analysis_end timestamptz,
  report_version text not null,
  status text not null,
  structured_report jsonb,
  generated_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists ai_visibility_runs_client_idx
  on public.ai_visibility_run_processing(client_id, source_created_at);
create index if not exists ai_visibility_queries_client_idx
  on public.ai_visibility_monitor_queries(client_id, service, method);
create index if not exists ai_visibility_answers_run_idx
  on public.ai_visibility_answers(run_id, answer_number);
create index if not exists ai_visibility_metrics_company_idx
  on public.ai_visibility_run_company_metrics(company_id, run_id);
create index if not exists ai_visibility_answer_mentions_company_idx
  on public.ai_visibility_answer_company_mentions(company_id, answer_id);
create index if not exists ai_visibility_citations_page_idx
  on public.ai_visibility_answer_citations(page_id, answer_id);
create index if not exists ai_visibility_jobs_ready_idx
  on public.ai_visibility_page_fetch_jobs(status, run_after, priority desc);
create index if not exists ai_visibility_snapshots_page_idx
  on public.ai_visibility_page_snapshots(page_id, retrieved_at desc);
create index if not exists ai_visibility_reports_client_idx
  on public.ai_visibility_reports(client_id, generated_at desc);
