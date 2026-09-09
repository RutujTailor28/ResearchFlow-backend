-- Migration: 005_create_findings_and_research_tasks
-- Phase 5 multi-agent research findings + per-sub-question task tracking

BEGIN;

CREATE TABLE IF NOT EXISTS public.findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES public.runs (id) ON DELETE CASCADE,
    section_id UUID NOT NULL REFERENCES public.plan_sections (id) ON DELETE CASCADE,
    claim TEXT NOT NULL,
    source_id UUID NULL REFERENCES public.sources (id) ON DELETE SET NULL,
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0.5,
    is_gap BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),

    CONSTRAINT findings_claim_length_check
        CHECK (char_length(btrim(claim)) >= 3 AND char_length(claim) <= 4000),
    CONSTRAINT findings_confidence_check
        CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_findings_run_id ON public.findings (run_id);
CREATE INDEX IF NOT EXISTS idx_findings_section_id ON public.findings (section_id);
CREATE INDEX IF NOT EXISTS idx_findings_source_id ON public.findings (source_id);

CREATE TABLE IF NOT EXISTS public.research_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES public.runs (id) ON DELETE CASCADE,
    section_id UUID NOT NULL REFERENCES public.plan_sections (id) ON DELETE CASCADE,
    sub_question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    tool_calls INTEGER NOT NULL DEFAULT 0,
    error TEXT NULL,
    gap_note TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    finished_at TIMESTAMPTZ NULL,

    CONSTRAINT research_tasks_status_check
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    CONSTRAINT research_tasks_tool_calls_check
        CHECK (tool_calls >= 0 AND tool_calls <= 20),
    CONSTRAINT research_tasks_sub_question_check
        CHECK (char_length(btrim(sub_question)) >= 3)
);

CREATE INDEX IF NOT EXISTS idx_research_tasks_run_id ON public.research_tasks (run_id);
CREATE INDEX IF NOT EXISTS idx_research_tasks_run_status ON public.research_tasks (run_id, status);

COMMENT ON TABLE public.findings IS 'Paraphrased research claims linked to sources (Phase 5)';
COMMENT ON TABLE public.research_tasks IS 'Per sub-question researcher task tracking (Phase 5)';

ALTER TABLE public.findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_tasks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS findings_service_role_all ON public.findings;
CREATE POLICY findings_service_role_all
    ON public.findings FOR ALL TO service_role
    USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS research_tasks_service_role_all ON public.research_tasks;
CREATE POLICY research_tasks_service_role_all
    ON public.research_tasks FOR ALL TO service_role
    USING (true) WITH CHECK (true);

COMMIT;
