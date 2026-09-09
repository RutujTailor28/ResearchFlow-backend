-- Migration: 007_create_reports_and_writer_events
-- Phase 7 report storage + writer event types

BEGIN;

-- Extend agent event types for writer lifecycle
ALTER TABLE public.agent_events
    DROP CONSTRAINT IF EXISTS agent_events_event_type_check;

ALTER TABLE public.agent_events
    ADD CONSTRAINT agent_events_event_type_check
    CHECK (event_type IN (
        'agent_started',
        'thinking',
        'tool_call',
        'tool_result',
        'agent_finished',
        'error',
        'retry',
        'writer_started',
        'section_started',
        'section_completed',
        'writer_finished'
    ));

CREATE TABLE IF NOT EXISTS public.reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES public.runs (id) ON DELETE CASCADE,
    markdown TEXT NOT NULL DEFAULT '',
    sections_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    sections_confidence_json JSONB NULL,
    unverified_claims_json JSONB NULL,
    citation_map_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),

    CONSTRAINT reports_run_id_unique UNIQUE (run_id),
    CONSTRAINT reports_markdown_length_check
        CHECK (char_length(markdown) <= 500000)
);

CREATE INDEX IF NOT EXISTS idx_reports_run_id ON public.reports (run_id);

COMMENT ON TABLE public.reports IS
    'Generated markdown research reports (Phase 7 Writer Agent)';
COMMENT ON COLUMN public.reports.sections_json IS
    'Per-section markdown bodies for targeted regenerate';
COMMENT ON COLUMN public.reports.citation_map_json IS
    'Maps citation index [n] -> source_id UUID string';

ALTER TABLE public.reports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS reports_service_role_all ON public.reports;
CREATE POLICY reports_service_role_all
    ON public.reports FOR ALL TO service_role
    USING (true) WITH CHECK (true);

COMMIT;
