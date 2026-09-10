-- ============================================================================
-- AI Career Intelligence Platform - Supabase schema
--
-- HOW TO RUN
--   1. Open your Supabase project
--   2. SQL Editor -> New query
--   3. Paste this whole file and press Run
--
-- Safe to re-run: everything is CREATE ... IF NOT EXISTS or DROP ... CREATE.
--
-- DESIGN NOTES
--   * Normalised, not one big JSON blob: action items, decisions, key points and
--     participants are queryable rows, which is what a dashboard needs.
--   * The transcript *text* lives on `meetings` (always read with the meeting)
--     while timed segments live in their own table (many rows, read only for the
--     timeline view).
--   * ON DELETE CASCADE everywhere, so deleting a meeting cleans up completely.
--   * `media_metadata` stays JSONB on purpose: ffprobe output is genuinely
--     schemaless and is only ever read as a whole.
-- ============================================================================

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------- meetings
create table if not exists public.meetings (
    id                       uuid primary key default gen_random_uuid(),
    title                    text        not null,
    original_filename        text        not null,
    file_extension           text,
    media_kind               text        not null default 'audio'
                                 check (media_kind in ('audio', 'video')),
    mime_type                text,
    file_size_bytes          bigint      not null default 0,
    duration_seconds         double precision,
    media_metadata           jsonb,

    status                   text        not null default 'UPLOADED'
                                 check (status in (
                                     'UPLOADED', 'VALIDATING', 'AUDIO_PROCESSING',
                                     'TRANSCRIBING', 'TRANSCRIPT_VALIDATED', 'AI_ANALYSIS',
                                     'PERSISTING', 'COMPLETED', 'FAILED')),
    error_code               text,
    error_message            text,

    transcript_text          text,
    transcript_language      text,
    transcript_word_count    integer,
    transcript_model         text,
    transcript_backend       text,
    transcript_generated_at  timestamptz,

    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now()
);

create index if not exists meetings_created_at_idx on public.meetings (created_at desc);
create index if not exists meetings_status_idx     on public.meetings (status);

-- ------------------------------------------------------ transcript_segments
create table if not exists public.transcript_segments (
    id             uuid primary key default gen_random_uuid(),
    meeting_id     uuid        not null references public.meetings (id) on delete cascade,
    segment_index  integer     not null,
    start_time     double precision not null default 0,
    end_time       double precision not null default 0,
    speaker        text,
    text           text        not null,
    confidence     double precision,
    created_at     timestamptz not null default now(),

    -- one row per position, so re-running transcription cannot duplicate segments
    constraint transcript_segments_unique_position unique (meeting_id, segment_index),
    constraint transcript_segments_time_order check (end_time >= start_time)
);

create index if not exists transcript_segments_meeting_idx
    on public.transcript_segments (meeting_id, segment_index);

-- ------------------------------------------------------------- participants
create table if not exists public.participants (
    id               uuid primary key default gen_random_uuid(),
    meeting_id       uuid        not null references public.meetings (id) on delete cascade,
    name             text        not null,
    normalized_name  text        not null,
    role             text,
    is_unknown       boolean     not null default false,
    mention_count    integer     not null default 1,
    aliases          text[]      not null default '{}',
    created_at       timestamptz not null default now(),

    -- the database-level guarantee that one person appears once per meeting
    constraint participants_unique_per_meeting unique (meeting_id, normalized_name)
);

create index if not exists participants_meeting_idx on public.participants (meeting_id);

-- ------------------------------------------------------------- action_items
create table if not exists public.action_items (
    id              uuid primary key default gen_random_uuid(),
    meeting_id      uuid        not null references public.meetings (id) on delete cascade,
    participant_id  uuid        references public.participants (id) on delete set null,
    position        integer     not null default 0,
    task            text        not null,
    assigned_to     text,                       -- null when the transcript never says
    deadline        text,                       -- kept as spoken: "Friday", "next sprint"
    priority        text        not null default 'medium'
                        check (priority in ('high', 'medium', 'low')),
    status          text        not null default 'pending'
                        check (status in ('pending', 'in_progress', 'completed', 'blocked')),
    context         text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists action_items_meeting_idx  on public.action_items (meeting_id, position);
create index if not exists action_items_status_idx   on public.action_items (status);

-- ---------------------------------------------------------------- decisions
create table if not exists public.decisions (
    id          uuid primary key default gen_random_uuid(),
    meeting_id  uuid        not null references public.meetings (id) on delete cascade,
    position    integer     not null default 0,
    text        text        not null,
    context     text,
    created_at  timestamptz not null default now()
);

create index if not exists decisions_meeting_idx on public.decisions (meeting_id, position);

-- --------------------------------------------------------------- key_points
create table if not exists public.key_points (
    id          uuid primary key default gen_random_uuid(),
    meeting_id  uuid        not null references public.meetings (id) on delete cascade,
    position    integer     not null default 0,
    text        text        not null,
    created_at  timestamptz not null default now()
);

create index if not exists key_points_meeting_idx on public.key_points (meeting_id, position);

-- -------------------------------------------------------- meeting_summaries
create table if not exists public.meeting_summaries (
    meeting_id    uuid primary key references public.meetings (id) on delete cascade,
    summary       text        not null,
    model         text,
    provider      text,                 -- grok | gemini | groq (which one answered)
    chunk_count   integer     not null default 1,
    generated_at  timestamptz not null default now()
);

-- Existing installations: `create table if not exists` will not add the column
-- to a table that already exists, so bring it in explicitly. Same statement as
-- database/migrations/001_add_provider_to_meeting_summaries.sql, and equally
-- safe to run repeatedly.
alter table public.meeting_summaries
    add column if not exists provider text;

-- ------------------------------------------------------- updated_at trigger
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists meetings_set_updated_at on public.meetings;
create trigger meetings_set_updated_at
    before update on public.meetings
    for each row execute function public.set_updated_at();

drop trigger if exists action_items_set_updated_at on public.action_items;
create trigger action_items_set_updated_at
    before update on public.action_items
    for each row execute function public.set_updated_at();

-- ------------------------------------------------------------------- views
-- Convenience view: one row per meeting with child counts, handy in the SQL
-- editor when demonstrating the data model.
create or replace view public.meeting_overview as
select
    m.id,
    m.title,
    m.status,
    m.media_kind,
    m.duration_seconds,
    m.transcript_word_count,
    (select count(*) from public.transcript_segments s where s.meeting_id = m.id) as segment_count,
    (select count(*) from public.action_items a       where a.meeting_id = m.id) as action_item_count,
    (select count(*) from public.participants p       where p.meeting_id = m.id) as participant_count,
    (select count(*) from public.decisions d          where d.meeting_id = m.id) as decision_count,
    (select count(*) from public.key_points k         where k.meeting_id = m.id) as key_point_count,
    m.created_at
from public.meetings m;

-- --------------------------------------------------------------------- RLS
-- Row Level Security is ON with no public policies. The backend uses the
-- service-role key, which bypasses RLS; anon/public keys therefore cannot read
-- or write anything. This is why the service-role key must never be shipped to
-- the React app. When per-user authentication is added later, add policies here
-- instead of turning RLS off.
alter table public.meetings            enable row level security;
alter table public.transcript_segments enable row level security;
alter table public.participants        enable row level security;
alter table public.action_items        enable row level security;
alter table public.decisions           enable row level security;
alter table public.key_points          enable row level security;
alter table public.meeting_summaries   enable row level security;

-- ------------------------------------------------------------ verification
-- Run this after the script to confirm all seven tables exist:
--
--   select table_name
--   from information_schema.tables
--   where table_schema = 'public'
--     and table_name in ('meetings','transcript_segments','participants',
--                        'action_items','decisions','key_points','meeting_summaries')
--   order by table_name;
