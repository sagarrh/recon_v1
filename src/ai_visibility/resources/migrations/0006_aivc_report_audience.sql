alter table public.aivc_final_reports
  add column if not exists report_audience text not null default 'internal';

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'aivc_final_reports_audience_check'
      and conrelid = 'public.aivc_final_reports'::regclass
  ) then
    alter table public.aivc_final_reports
      add constraint aivc_final_reports_audience_check
      check (report_audience in ('client', 'internal'));
  end if;
end $$;

create index if not exists aivc_final_reports_parent_profile_audience_idx
  on public.aivc_final_reports(parent_run_id, report_profile, report_audience);
