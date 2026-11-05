-- SafePay durable state. Run once in the Supabase SQL editor.
--
-- Three tables back the in-memory structures when SUPABASE_URL and
-- SUPABASE_SERVICE_ROLE_KEY are set: the hash-chained audit log, the spend
-- ledger, and the human-approval queue. RLS is enabled with no policies, so
-- only the service role key (used server-side) can read or write them; the
-- anon key sees nothing.

create table if not exists safepay_audit (
  seq        integer primary key,          -- the chain position; unique, so two
  prev       text    not null,             --   instances cannot both claim a link
  sha256     text    not null,
  record     jsonb   not null,             -- the full signed record
  created_at timestamptz not null default now()
);

create table if not exists safepay_ledger (
  id        bigserial primary key,
  agent_id  text   not null,
  recipient text   not null default '',
  amount    double precision not null,
  ts        double precision not null,     -- epoch seconds, UTC
  merchant  text   not null default '',    -- for the payment-history rules
  invoice_ref text not null default ''     -- duplicate-invoice rule
);
-- Upgrading an existing project:
alter table safepay_ledger add column if not exists merchant text not null default '';
alter table safepay_ledger add column if not exists invoice_ref text not null default '';
create index if not exists safepay_ledger_agent_ts on safepay_ledger (agent_id, ts);

create table if not exists safepay_approvals (
  id          text primary key,
  status      text  not null,              -- pending | approved | rejected
  decision    jsonb not null,
  note        text  not null default '',
  execution   jsonb,
  created_ts  double precision not null,
  resolved_ts double precision
);
create index if not exists safepay_approvals_status on safepay_approvals (status, created_ts);

-- Early-access sign-ups from the web console. Write-only from the app (no
-- public endpoint lists them); email is unique so a repeat sign-up updates
-- the row instead of adding one.
create table if not exists safepay_leads (
  id             bigserial primary key,
  email          text  not null unique,
  company        text  not null default '',
  role           text  not null default '',
  use_case       text  not null default '',
  monthly_volume text  not null default 'unknown',
  source         text  not null default 'console',
  created_at     timestamptz not null default now()
);

-- Operator controls. settings holds the policy a console run applied
-- (key 'policy:<run>'); agents is the registry: every agent id the guardrail
-- has seen, with its status (active | paused) and the operator's reason.
create table if not exists safepay_settings (
  key        text primary key,
  value      jsonb not null,
  updated_ts double precision not null
);

create table if not exists safepay_agents (
  agent_id   text primary key,
  run        text not null default '',
  status     text not null default 'active',
  reason     text not null default '',
  first_seen double precision not null,
  last_seen  double precision not null,
  status_ts  double precision,
  decisions  integer not null default 0,
  has_key    boolean not null default false   -- an agent key is issued (hash in settings)
);
create index if not exists safepay_agents_run on safepay_agents (run, first_seen);

alter table safepay_settings  enable row level security;
alter table safepay_agents    enable row level security;
grant all on table safepay_settings, safepay_agents to service_role;

alter table safepay_audit     enable row level security;
alter table safepay_ledger    enable row level security;
alter table safepay_approvals enable row level security;
alter table safepay_leads     enable row level security;

-- The server uses the service role key, which bypasses RLS. Grant it access
-- explicitly so this works whether or not the project auto-exposes new tables
-- to the Data API roles. anon/authenticated get nothing: RLS with no policies.
grant usage on schema public to service_role;
grant all on table safepay_audit, safepay_ledger, safepay_approvals, safepay_leads to service_role;
grant usage, select on sequence safepay_ledger_id_seq, safepay_leads_id_seq to service_role;
