-- Migration: 008_verification_fields_and_events
-- Phase 8 verifier quality fields on reports + verifier event types

BEGIN;

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
        'writer_finished',
        'verifier_started',
        'verifier_finished'
    ));

ALTER TABLE public.reports
    ADD COLUMN IF NOT EXISTS conflicts_json JSONB NULL;

ALTER TABLE public.reports
    ADD COLUMN IF NOT EXISTS verification_json JSONB NULL;

ALTER TABLE public.reports
    ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN public.reports.conflicts_json IS
    'Source disagreement records from Verifier Agent (Phase 8)';
COMMENT ON COLUMN public.reports.verification_json IS
    'Full verification summary: status, repetitions, counts (Phase 8)';
COMMENT ON COLUMN public.reports.unverified_claims_json IS
    'Unsupported claims with section/reason/suggested evidence gap (Phase 7+8)';
COMMENT ON COLUMN public.reports.sections_confidence_json IS
    'Per-section confidence labels high|medium|low plus metrics (Phase 7+8)';

COMMIT;
