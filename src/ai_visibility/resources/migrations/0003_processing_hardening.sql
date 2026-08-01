alter table public.ai_visibility_signals
  add column if not exists status text not null default 'active';

create index if not exists ai_visibility_signals_active_idx
  on public.ai_visibility_signals(client_id, company_id, status, updated_at desc);
