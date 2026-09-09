-- Migration: 004_create_sources
-- Phase 4 research source storage
-- Apply via Supabase SQL Editor if DATABASE_URL is unset

BEGIN;

CREATE TABLE IF NOT EXISTS public.sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES public.runs (id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    published_at TIMESTAMPTZ NULL,
    quality_score NUMERIC(5, 2) NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    extract_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),

    CONSTRAINT sources_url_length_check
        CHECK (char_length(btrim(url)) >= 8 AND char_length(url) <= 2048),
    CONSTRAINT sources_domain_length_check
        CHECK (char_length(btrim(domain)) >= 1 AND char_length(domain) <= 255),
    CONSTRAINT sources_quality_score_check
        CHECK (quality_score >= 0 AND quality_score <= 100),
    CONSTRAINT sources_run_url_unique UNIQUE (run_id, url),
    CONSTRAINT sources_run_content_hash_unique UNIQUE (run_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_sources_run_id ON public.sources (run_id);
CREATE INDEX IF NOT EXISTS idx_sources_domain ON public.sources (domain);
CREATE INDEX IF NOT EXISTS idx_sources_quality_score
    ON public.sources (run_id, quality_score DESC);

COMMENT ON TABLE public.sources IS 'Fetched/extracted research sources for a run (Phase 4)';

ALTER TABLE public.sources ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sources_service_role_all ON public.sources;
CREATE POLICY sources_service_role_all
    ON public.sources
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

COMMIT;
