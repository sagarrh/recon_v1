-- 0008_aivc_action_executions.sql
-- Records WHEN a recommendation was actually implemented.
--
-- Without this, every outcome Recon reports means "SOV moved after we said something", not "after
-- the client did something". outcome_measure previously anchored its window on the recommendation's
-- own week_date and wrote `executed: null` because it had no way to know. This table is that way.
--
-- Owned by the aivc CLI over the direct PostgreSQL connection, not by Scout's supabase-py writer:
-- Scout's tables live outside this repository's migration runner, and an execution record is
-- operator input rather than pipeline output. Scout reads it back via PostgREST in the same project.

create table if not exists public.aivc_action_executions (
  id uuid primary key default gen_random_uuid(),
  -- Polymorphic on purpose: citation-report signals become measurable subjects later, and the
  -- measurement engine treats both the same way.
  subject_type text not null check (subject_type in ('recon_recommendation', 'citation_signal')),
  subject_id text not null,
  client_id uuid not null,
  status text not null default 'proposed' check (
    status in ('proposed', 'accepted', 'executed', 'verified', 'abandoned')
  ),
  -- The measurement anchor. NULL until someone confirms the change actually shipped; the follow-up
  -- window is counted from this instant and from nothing else.
  implemented_at timestamptz,
  implemented_by text,
  -- Frozen at execution time. The recommendation may be edited or superseded afterwards, but what
  -- was measured must remain what was actually changed.
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
  updated_at timestamptz not null default now(),
  unique (subject_type, subject_id)
);

-- A claim of execution without a timestamp is not measurable, and silently measuring from "now"
-- would attribute pre-existing movement to the action. Enforced rather than left to the caller.
alter table public.aivc_action_executions
  drop constraint if exists aivc_action_executions_executed_requires_timestamp;
alter table public.aivc_action_executions
  add constraint aivc_action_executions_executed_requires_timestamp check (
    status not in ('executed', 'verified') or implemented_at is not null
  );

create index if not exists aivc_action_executions_client_status_idx
  on public.aivc_action_executions(client_id, status);

-- The measurement sweep's access pattern: everything executed whose window may have elapsed.
create index if not exists aivc_action_executions_implemented_idx
  on public.aivc_action_executions(implemented_at)
  where implemented_at is not null;

alter table public.aivc_action_executions enable row level security;

create policy aivc_action_executions_tenant_select
  on public.aivc_action_executions for select
  using (client_id = public.ai_visibility_current_client_id());
