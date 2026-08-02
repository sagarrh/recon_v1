-- REVIEW-ONLY DESTRUCTIVE CLEANUP
--
-- This script is intentionally not a migration and is never run by the application.
-- Back up the aivc_* tables before use. To unlock it in an explicitly approved session:
--
--   SET aivc.cleanup_approved = 'yes';
--   \i sql/aivc_cleanup_20260802_review.sql

begin;

set local lock_timeout = '5s';
set local statement_timeout = '60s';

do $$
begin
  if current_setting('aivc.cleanup_approved', true) is distinct from 'yes' then
    raise exception 'Cleanup is locked. Set aivc.cleanup_approved=yes after approval.';
  end if;

  if not exists (
    select 1
    from public.aivc_pipeline_runs
    where id = '7907efe8-2b44-49cd-bcc9-f205bf460858'::uuid
      and client_id = 'b88e87f3-0aa5-4da9-be48-2807b12d5a91'::uuid
      and canonical_name = 'Aprio'
      and status in ('completed', 'partial')
  ) then
    raise exception 'The audited Aprio parent run no longer matches; aborting cleanup.';
  end if;

  if not exists (
    select 1
    from public.aivc_final_reports
    where parent_run_id = '7907efe8-2b44-49cd-bcc9-f205bf460858'::uuid
      and report_profile = 'detailed'
      and report_audience = 'client'
      and config_version = '1.3'
  ) then
    raise exception 'Regenerate the retained Aprio report with code/config 1.3 first.';
  end if;
end $$;

-- Remove report-mode variants and retain only the newest current report.
with ranked as (
  select id,
         row_number() over (
           partition by parent_run_id
           order by generated_at desc nulls last, updated_at desc, id
         ) as row_number
  from public.aivc_final_reports
  where parent_run_id = '7907efe8-2b44-49cd-bcc9-f205bf460858'::uuid
)
delete from public.aivc_final_reports report
using ranked
where report.id = ranked.id
  and ranked.row_number > 1;

delete from public.aivc_final_reports
where parent_run_id <> '7907efe8-2b44-49cd-bcc9-f205bf460858'::uuid;

-- Combined bundles duplicate the two authoritative producer ledgers.
delete from public.aivc_signal_bundles
where producer = 'aivc_combined'
   or parent_run_id <> '7907efe8-2b44-49cd-bcc9-f205bf460858'::uuid
   or parent_run_id is null;

-- Remove development/orphan parents. Dependent stage rows cascade.
delete from public.aivc_pipeline_runs
where id in (
  '6fcfa1f8-fc11-4990-b43d-0e0cadbf6673'::uuid,
  'd2c5e65b-971d-44c2-8caa-2c8a8eff17ed'::uuid,
  '277eee2a-6c16-4e5e-be53-d99f327d75f0'::uuid,
  'f26a6e71-ad04-4f0d-9c7b-c53a7aaca396'::uuid,
  'f2278762-26b5-4e9b-b740-3879c0391305'::uuid
);

delete from public.aivc_pipeline_stages
where parent_run_id = '7907efe8-2b44-49cd-bcc9-f205bf460858'::uuid
  and stage_name in ('compose', 'decision_cards');

drop table public.aivc_delivery_log;

alter table public.aivc_pipeline_runs
  drop column combined_bundle_id,
  drop column combined_bundle_checksum;

create unique index aivc_final_reports_one_per_parent_idx
  on public.aivc_final_reports(parent_run_id);

-- The intended post-cleanup state is one parent, two source bundles, and one final report.
do $$
declare
  parent_count integer;
  bundle_count integer;
  report_count integer;
begin
  select count(*) into parent_count from public.aivc_pipeline_runs;
  select count(*) into bundle_count from public.aivc_signal_bundles;
  select count(*) into report_count from public.aivc_final_reports;
  if parent_count <> 1 or bundle_count <> 2 or report_count <> 1 then
    raise exception 'Unexpected post-cleanup counts: parents %, bundles %, reports %',
      parent_count, bundle_count, report_count;
  end if;
end $$;

commit;
