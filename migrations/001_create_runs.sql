-- Migration: 001_create_runs
-- Phase 1 core run management table for Multi-Agent Research API
-- Apply via: python scripts/apply_migrations.py
-- Or paste into Supabase Dashboard → SQL Editor

BEGIN;

CREATE TABLE IF NOT EXISTS public.runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NULL,
    topic TEXT NOT NULL,
    depth TEXT NOT NULL,
    audience TEXT NOT NULL,
    tone TEXT NOT NULL,
    target_length INTEGER NOT NULL DEFAULT 3000,
    source_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'plan_pending',
    plan_json JSONB NULL,
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    duration_ms INTEGER NULL,
    error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    finished_at TIMESTAMPTZ NULL,

    CONSTRAINT runs_topic_length_check
        CHECK (char_length(btrim(topic)) >= 3 AND char_length(topic) <= 500),
    CONSTRAINT runs_depth_check
        CHECK (depth IN ('quick', 'standard', 'deep')),
    CONSTRAINT runs_status_check
        CHECK (status IN (
            'draft',
            'plan_pending',
            'plan_ready',
            'approved',
            'running',
            'completed',
            'failed',
            'cancelled',
            'partial'
        )),
    CONSTRAINT runs_target_length_check
        CHECK (target_length >= 500 AND target_length <= 20000)
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON public.runs (status);
CREATE INDEX IF NOT EXISTS idx_runs_created_at_desc ON public.runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_user_id ON public.runs (user_id);

COMMENT ON TABLE public.runs IS 'Research runs for Multi-Agent Research API (Phase 1)';

-- Service-role backend access; tighten with RLS when auth lands.
ALTER TABLE public.runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS runs_service_role_all ON public.runs;
CREATE POLICY runs_service_role_all
    ON public.runs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Allow authenticated users to manage their own rows later (user_id match).
DROP POLICY IF EXISTS runs_authenticated_own ON public.runs;
CREATE POLICY runs_authenticated_own
    ON public.runs
    FOR ALL
    TO authenticated
    USING (user_id IS NOT NULL AND auth.uid() = user_id)
    WITH CHECK (user_id IS NOT NULL AND auth.uid() = user_id);

COMMIT;
