-- Reference only. Adapt to repository conventions and existing user/client tables.

create table if not exists public.monitor_queries (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null,
  cluster_id text,
  base_query text not null,
  normalized_base_query text not null,
  service text not null,
  method text not null,
  configuration_hash text not null,
  configuration_completeness text not null default 'incomplete',
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (client_id, normalized_base_query, service, method, configuration_hash)
);

create table if not exists public.monitor_run_processing (
  run_id uuid primary key references public.ai_monitoring(id) on delete cascade,
  monitor_query_id uuid references public.monitor_queries(id),
  status text not null default 'pending',
  is_valid boolean,
  invalid_reason text,
  pipeline_version text not null,
  configuration_completeness text,
  normalized_at timestamptz,
  compared_at timestamptz,
  signal_generated_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.monitor_answers (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.ai_monitoring(id) on delete cascade,
  answer_number integer not null,
  answer_text text not null,
  answer_hash text not null,
  word_count integer not null default 0,
  created_at timestamptz not null default now(),
  unique (run_id, answer_number)
);

create table if not exists public.companies (
  id uuid primary key default gen_random_uuid(),
  canonical_name text not null,
  normalized_name text not null unique,
  official_domain text,
  company_type text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.company_aliases (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  alias text not null,
  normalized_alias text not null unique,
  is_primary boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.client_companies (
  client_id uuid not null,
  company_id uuid not null references public.companies(id) on delete cascade,
  relationship text not null check (relationship in ('client','competitor','partner','other_tracked')),
  priority integer not null default 0,
  created_at timestamptz not null default now(),
  primary key (client_id, company_id)
);

create table if not exists public.answer_company_mentions (
  id uuid primary key default gen_random_uuid(),
  answer_id uuid not null references public.monitor_answers(id) on delete cascade,
  company_id uuid not null references public.companies(id) on delete cascade,
  literal_mention_count integer not null default 0,
  first_position integer,
  context_snippets jsonb not null default '[]'::jsonb,
  mention_type text,
  sentiment text,
  prominence_score numeric,
  detection_method text not null,
  confidence numeric,
  created_at timestamptz not null default now(),
  unique (answer_id, company_id, detection_method)
);

create table if not exists public.run_company_metrics (
  run_id uuid not null references public.ai_monitoring(id) on delete cascade,
  company_id uuid not null references public.companies(id) on delete cascade,
  literal_answer_count integer not null,
  literal_visibility numeric not null,
  literal_total_mentions integer not null,
  upstream_count numeric,
  upstream_visibility numeric,
  upstream_word_count numeric,
  metric_difference numeric,
  data_quality_flags jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  primary key (run_id, company_id)
);

create table if not exists public.citation_pages (
  id uuid primary key default gen_random_uuid(),
  normalized_url text not null unique,
  canonical_url text,
  domain text not null,
  publisher_company_id uuid references public.companies(id),
  source_type text,
  content_type text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.answer_citations (
  id uuid primary key default gen_random_uuid(),
  answer_id uuid not null references public.monitor_answers(id) on delete cascade,
  page_id uuid not null references public.citation_pages(id) on delete cascade,
  original_url text not null,
  title text,
  url_class text,
  raw_occurrence_count integer not null default 1,
  valid_start_index integer,
  valid_end_index integer,
  position_quality text not null,
  created_at timestamptz not null default now(),
  unique (answer_id, page_id)
);

create table if not exists public.run_comparisons (
  id uuid primary key default gen_random_uuid(),
  monitor_query_id uuid not null references public.monitor_queries(id),
  previous_run_id uuid not null references public.ai_monitoring(id),
  current_run_id uuid not null references public.ai_monitoring(id),
  comparison_type text not null,
  comparability_score numeric not null,
  comparability_flags jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique (previous_run_id, current_run_id, comparison_type)
);

create table if not exists public.comparison_company_deltas (
  comparison_id uuid not null references public.run_comparisons(id) on delete cascade,
  company_id uuid not null references public.companies(id),
  previous_literal_count integer not null,
  current_literal_count integer not null,
  answer_count_delta integer not null,
  previous_visibility numeric not null,
  current_visibility numeric not null,
  visibility_delta numeric not null,
  status text not null,
  severity numeric not null,
  created_at timestamptz not null default now(),
  primary key (comparison_id, company_id)
);

create table if not exists public.comparison_url_deltas (
  comparison_id uuid not null references public.run_comparisons(id) on delete cascade,
  page_id uuid not null references public.citation_pages(id),
  previous_answer_coverage integer not null,
  current_answer_coverage integer not null,
  coverage_delta integer not null,
  previous_raw_occurrences integer not null,
  current_raw_occurrences integer not null,
  raw_occurrence_delta integer not null,
  status text not null,
  created_at timestamptz not null default now(),
  primary key (comparison_id, page_id)
);

create table if not exists public.page_fetch_jobs (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references public.citation_pages(id) on delete cascade,
  priority integer not null default 0,
  reason text not null,
  status text not null default 'pending',
  attempts integer not null default 0,
  run_after timestamptz not null default now(),
  locked_at timestamptz,
  locked_by text,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.page_snapshots (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references public.citation_pages(id) on delete cascade,
  retrieved_at timestamptz not null default now(),
  http_status integer,
  final_url text,
  title text,
  author text,
  published_at timestamptz,
  modified_at timestamptz,
  main_text text,
  main_text_hash text,
  raw_content_hash text,
  etag text,
  last_modified_header text,
  structured_data jsonb,
  fetch_method text,
  fetch_error text,
  robots_allowed boolean,
  created_at timestamptz not null default now()
);

create table if not exists public.page_company_mentions (
  snapshot_id uuid not null references public.page_snapshots(id) on delete cascade,
  company_id uuid not null references public.companies(id),
  mention_count integer not null default 0,
  mentioned_in_title boolean not null default false,
  mentioned_in_heading boolean not null default false,
  links_to_official_domain boolean not null default false,
  publisher_is_company boolean not null default false,
  mention_role text,
  sentiment text,
  prominence_score numeric,
  context_snippets jsonb not null default '[]'::jsonb,
  confidence numeric,
  created_at timestamptz not null default now(),
  primary key (snapshot_id, company_id)
);

create table if not exists public.page_snapshot_diffs (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references public.citation_pages(id) on delete cascade,
  previous_snapshot_id uuid not null references public.page_snapshots(id),
  current_snapshot_id uuid not null references public.page_snapshots(id),
  text_similarity numeric,
  material_change boolean not null,
  change_summary jsonb not null default '{}'::jsonb,
  companies_added jsonb not null default '[]'::jsonb,
  companies_removed jsonb not null default '[]'::jsonb,
  company_prominence_changes jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique (previous_snapshot_id, current_snapshot_id)
);

create table if not exists public.signals (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null,
  monitor_query_id uuid not null references public.monitor_queries(id),
  comparison_id uuid references public.run_comparisons(id),
  company_id uuid references public.companies(id),
  signal_type text not null,
  severity numeric not null,
  confidence text not null,
  title text not null,
  summary text not null,
  primary_hypothesis text,
  alternative_explanations jsonb not null default '[]'::jsonb,
  recommended_actions jsonb not null default '[]'::jsonb,
  data_quality_flags jsonb not null default '[]'::jsonb,
  structured_payload jsonb not null,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.signal_evidence (
  id uuid primary key default gen_random_uuid(),
  signal_id uuid not null references public.signals(id) on delete cascade,
  evidence_type text not null,
  entity_id uuid,
  weight numeric not null,
  supports_hypothesis boolean not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists public.company_intelligence_reports (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null,
  company_id uuid not null references public.companies(id),
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

create index if not exists idx_monitor_queries_client on public.monitor_queries(client_id, service, method);
create index if not exists idx_monitor_answers_run on public.monitor_answers(run_id, answer_number);
create index if not exists idx_run_company_metrics_company on public.run_company_metrics(company_id, run_id);
create index if not exists idx_answer_mentions_company on public.answer_company_mentions(company_id, answer_id);
create index if not exists idx_answer_citations_page on public.answer_citations(page_id, answer_id);
create index if not exists idx_comparisons_query on public.run_comparisons(monitor_query_id, current_run_id);
create index if not exists idx_page_jobs_ready on public.page_fetch_jobs(status, run_after, priority desc);
create index if not exists idx_page_snapshots_page on public.page_snapshots(page_id, retrieved_at desc);
create index if not exists idx_signals_client on public.signals(client_id, created_at desc);
create index if not exists idx_reports_client on public.company_intelligence_reports(client_id, generated_at desc);
