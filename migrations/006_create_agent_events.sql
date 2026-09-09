-- Migration: 006_create_agent_events
-- Phase 6 append-only agent observability events

BEGIN;

CREATE TABLE IF NOT EXISTS public.agent_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES public.runs (id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    agent_role TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),

    CONSTRAINT agent_events_agent_name_check
        CHECK (char_length(btrim(agent_name)) >= 1 AND char_length(agent_name) <= 100),
    CONSTRAINT agent_events_agent_role_check
        CHECK (agent_role IN (
            'planner', 'researcher', 'writer', 'verifier', 'orchestrator'
        )),
    CONSTRAINT agent_events_event_type_check
        CHECK (event_type IN (
            'agent_started',
            'thinking',
            'tool_call',
            'tool_result',
            'agent_finished',
            'error',
            'retry'
        )),
    CONSTRAINT agent_events_tokens_in_check CHECK (tokens_in >= 0),
    CONSTRAINT agent_events_tokens_out_check CHECK (tokens_out >= 0),
    CONSTRAINT agent_events_cost_check CHECK (cost_usd >= 0)
);

-- Chronological replay / pagination
CREATE INDEX IF NOT EXISTS idx_agent_events_run_created
    ON public.agent_events (run_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_agent_events_run_agent
    ON public.agent_events (run_id, agent_name, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_agent_events_run_type
    ON public.agent_events (run_id, event_type, created_at ASC);

COMMENT ON TABLE public.agent_events IS
    'Append-only agent observability events for SSE + historical replay (Phase 6)';

ALTER TABLE public.agent_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_events_service_role_all ON public.agent_events;
CREATE POLICY agent_events_service_role_all
    ON public.agent_events FOR ALL TO service_role
    USING (true) WITH CHECK (true);

-- Append-only: block updates/deletes for authenticated roles if ever exposed.
-- Service role retains full access for run cascade cleanup.

COMMIT;
