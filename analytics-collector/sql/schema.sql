create table if not exists events (
  id bigserial primary key,
  received_at timestamptz not null default now(),
  event_name text not null,
  session_id text not null,
  page_path text not null,
  occurred_at timestamptz not null,
  accession_number text null,
  form_type text null,
  tracked_name text null,
  referrer_domain text null,
  viewport_class text null,
  search_length_bucket text null,
  user_agent_family text null
);

create index if not exists idx_events_received_at on events (received_at desc);
create index if not exists idx_events_event_name on events (event_name);
create index if not exists idx_events_session_id on events (session_id);
create index if not exists idx_events_accession_number on events (accession_number);
