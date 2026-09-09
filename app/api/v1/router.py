"""Aggregate API v1 routers."""

from fastapi import APIRouter

from app.api.v1 import (
    agents_events,
    findings,
    health,
    plans,
    reports,
    research,
    run_research,
    runs,
    sections,
    sources,
    usage_export,
    verification,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(runs.router)
api_router.include_router(plans.router)
api_router.include_router(sections.router)
api_router.include_router(sources.router)
api_router.include_router(research.router)
api_router.include_router(run_research.router)
api_router.include_router(findings.router)
api_router.include_router(agents_events.router)
api_router.include_router(reports.router)
api_router.include_router(verification.router)
api_router.include_router(usage_export.router)
