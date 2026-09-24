-- ---------------------------------------------------------------------------
-- Migration 002 - knowledge index bookkeeping (Milestone 3)
--
-- PURPOSE
--   Track whether each meeting's knowledge has been turned into vectors, and
--   remember a fingerprint of the content that was indexed. The fingerprint is
--   what lets a re-index skip meetings whose content has not changed, so a
--   backfill over already-indexed meetings costs zero embedding API calls.
--
-- WHY THIS IS THE ONLY SCHEMA CHANGE IN MILESTONE 3
--   Supabase stays the source of truth for meetings; Pinecone only holds
--   vectors rebuilt from these tables. No table stores embeddings, and no
--   existing table or column is altered or dropped.
--
-- HOW TO RUN
--   Supabase -> SQL Editor -> New query -> paste -> Run.
--   Safe to run more than once. It adds nullable columns only: no existing row
--   is modified and no data is destroyed.
--
-- OPTIONAL
--   The application works without this migration. If the columns are missing,
--   indexing still runs and search still works - the backend logs a warning and
--   simply cannot remember what it already indexed, which means re-indexing
--   re-embeds instead of skipping.
-- ---------------------------------------------------------------------------

alter table public.meetings
    add column if not exists index_status          text,
    add column if not exists indexed_at            timestamptz,
    add column if not exists index_error           text,
    add column if not exists knowledge_fingerprint text;

comment on column public.meetings.index_status is
    'Knowledge index state: NOT_INDEXED | INDEXING | INDEXED | FAILED.';
comment on column public.meetings.knowledge_fingerprint is
    'SHA-256 of the indexed content. Unchanged fingerprint = no re-embedding.';

-- Finding meetings still to index should not scan the table.
create index if not exists meetings_index_status_idx
    on public.meetings (index_status);

-- Verify:
--   select column_name from information_schema.columns
--   where table_schema = 'public' and table_name = 'meetings'
--     and column_name in ('index_status','indexed_at','index_error','knowledge_fingerprint');
