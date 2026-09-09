-- Migration: 002_create_plan_sections
-- Phase 2 plan sections for Multi-Agent Research API
-- Apply via: python scripts/apply_migrations.py
-- Or paste into Supabase Dashboard → SQL Editor

BEGIN;

CREATE TABLE IF NOT EXISTS public.plan_sections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES public.runs (id) ON DELETE CASCADE,
    order_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    sub_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),

    CONSTRAINT plan_sections_order_index_check CHECK (order_index >= 1),
    CONSTRAINT plan_sections_title_length_check
        CHECK (char_length(btrim(title)) >= 1 AND char_length(title) <= 300),
    CONSTRAINT plan_sections_status_check
        CHECK (status IN (
            'pending',
            'researching',
            'writing',
            'verifying',
            'completed',
            'failed'
        )),
    CONSTRAINT plan_sections_run_order_unique UNIQUE (run_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_plan_sections_run_id ON public.plan_sections (run_id);
CREATE INDEX IF NOT EXISTS idx_plan_sections_run_order
    ON public.plan_sections (run_id, order_index);

COMMENT ON TABLE public.plan_sections IS 'Ordered research plan sections for a run (Phase 2)';

ALTER TABLE public.plan_sections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS plan_sections_service_role_all ON public.plan_sections;
CREATE POLICY plan_sections_service_role_all
    ON public.plan_sections
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

COMMIT;
