create table if not exists public.opportunity_review_status (
  review_key text primary key,
  source text,
  notice_id text,
  title text,
  url text,

  review_status text,
  reviewer_fit text,
  tech_owner text,
  admin_owner text,

  admin_reviewed boolean not null default false,
  emv_technical_reviewed boolean not null default false,
  commissioning_technical_reviewed boolean not null default false,

  technical_review_notes text,
  admin_review_notes text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  updated_by text
);

create index if not exists idx_opportunity_review_status_source
on public.opportunity_review_status (source);

create index if not exists idx_opportunity_review_status_notice_id
on public.opportunity_review_status (notice_id);

grant usage on schema public to service_role;

grant select, insert, update, delete
on public.opportunity_review_status
to service_role;
