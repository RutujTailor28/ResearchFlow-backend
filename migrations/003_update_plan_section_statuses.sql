-- Migration: 003_update_plan_section_statuses
-- Phase 3 section statuses for research preparation
-- Apply via Supabase SQL Editor if DATABASE_URL is unset

BEGIN;

ALTER TABLE public.plan_sections
    DROP CONSTRAINT IF EXISTS plan_sections_status_check;

-- Normalize legacy status values if any exist
UPDATE public.plan_sections
SET status = 'pending'
WHERE status NOT IN (
    'pending',
    'researching',
    'researched',
    'writing',
    'completed',
    'failed'
);

ALTER TABLE public.plan_sections
    ADD CONSTRAINT plan_sections_status_check
    CHECK (status IN (
        'pending',
        'researching',
        'researched',
        'writing',
        'completed',
        'failed'
    ));

COMMIT;
