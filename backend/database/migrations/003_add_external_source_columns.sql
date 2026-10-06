-- Migration 003: Add external source columns for integrations (Zoom, Google Meet)

ALTER TABLE public.meetings
ADD COLUMN external_source text,
ADD COLUMN external_source_id text,
ADD COLUMN external_metadata jsonb;

CREATE UNIQUE INDEX idx_meetings_external_source 
ON public.meetings (external_source, external_source_id) 
WHERE external_source IS NOT NULL AND external_source_id IS NOT NULL;
