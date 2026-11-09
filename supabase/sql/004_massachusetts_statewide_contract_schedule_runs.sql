-- 004_massachusetts_statewide_contract_schedule_runs.sql
-- Store one sanitized operational record per Massachusetts schedule check.
-- PDF rows, contact details, credentials, and raw response bodies are not stored.

create table if not exists public.massachusetts_statewide_contract_schedule_runs (
    id bigserial primary key,
    status text not null check (status in ('ok', 'error')),
    parser_version text not null,

    github_run_id text,
    github_run_attempt text,
    github_workflow text,
    github_event_name text,

    checked_at timestamptz not null,
    completed_at timestamptz not null,

    source_url text not null,
    final_url text,
    http_status integer,
    content_type text,
    content_bytes integer,
    content_sha256 text,
    pdf_page_count integer,

    schedule_period_start date,
    schedule_period_end date,
    source_document_updated text,
    structured_row_count integer,
    potentially_relevant_row_count integer,
    commbuys_row_count integer,
    repeated_document_number_count integer,
    digest_status text check (digest_status in ('new', 'none', 'not_checked')),
    relevant_row_fingerprints jsonb not null default '[]'::jsonb,
    new_relevant_row_count integer,

    error_type text,
    error_message text,
    created_at timestamptz not null default now()
);

create index if not exists idx_ma_contract_schedule_runs_checked
    on public.massachusetts_statewide_contract_schedule_runs (checked_at desc);

create index if not exists idx_ma_contract_schedule_runs_github_run
    on public.massachusetts_statewide_contract_schedule_runs (github_run_id);

alter table public.massachusetts_statewide_contract_schedule_runs
    enable row level security;

grant usage on schema public to service_role;

grant select, insert
on public.massachusetts_statewide_contract_schedule_runs
to service_role;

grant usage, select
on sequence public.massachusetts_statewide_contract_schedule_runs_id_seq
to service_role;

comment on table public.massachusetts_statewide_contract_schedule_runs is
'Sanitized operational history for Massachusetts Statewide Contract Schedule checks; source rows and contacts are not persisted.';

-- No anon or authenticated policy is added. The static public page contains
-- only sanitized rendered content and never queries this table from a browser.
