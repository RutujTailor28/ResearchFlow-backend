-- Migration: 009_reliability_cancel_cost
-- Phase 10: cancellation flag, cost ceiling, run timeout tracking
-- Apply in Supabase SQL Editor after 008

BEGIN;

ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS cost_limit_usd NUMERIC(12, 6) NULL;

ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL;

ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS stop_reason TEXT NULL;

COMMENT ON COLUMN public.runs.cancel_requested IS
    'Cooperative cancel flag; workers check between steps (Phase 10)';
COMMENT ON COLUMN public.runs.cost_limit_usd IS
    'Optional per-run cost ceiling USD; NULL uses global RUN_COST_LIMIT_USD';
COMMENT ON COLUMN public.runs.started_at IS
    'When research entered running (for global run timeout)';
COMMENT ON COLUMN public.runs.stop_reason IS
    'Why a run stopped early: cancelled, cost_limit, timeout, etc.';

COMMIT;
