-- ---------------------------------------------------------------------------
-- Migration 001 - record which AI provider produced each analysis
--
-- Added with the multi-model fallback (Grok -> Gemini -> Groq). The column is
-- nullable and purely informational: the application works without it (the
-- repository detects the missing column and stores the summary anyway), but
-- running this makes "AI Provider Used: ..." accurate in the UI and lets you
-- see from the database which provider answered.
--
-- Safe to run more than once. Run it in the Supabase SQL editor.
-- ---------------------------------------------------------------------------

alter table public.meeting_summaries
    add column if not exists provider text;

comment on column public.meeting_summaries.provider is
    'AI provider that produced this analysis: grok | gemini | groq.';

-- Verify:
--   select column_name from information_schema.columns
--   where table_schema = 'public' and table_name = 'meeting_summaries';
