-- Add reversible main-dashboard visibility to existing review records.
--
-- Hiding an opportunity must not delete it from opportunity_seen or
-- opportunity_active. The static dashboards load this flag with the other
-- team-review fields and move hidden rows into a collapsed, restorable list.
-- The statement is idempotent so it is safe to run on both existing and newly
-- created projects (001 already includes the column for fresh installations).

alter table public.opportunity_review_status
add column if not exists dashboard_hidden boolean not null default false;

comment on column public.opportunity_review_status.dashboard_hidden is
'True when the opportunity is intentionally hidden from the main dashboard table; underlying opportunity and review records are retained.';
