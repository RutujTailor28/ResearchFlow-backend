"""Health check endpoint."""

from fastapi import APIRouter

from app import __version__
from app.core.config import get_settings
from app.core.supabase import check_supabase_connection
from app.repositories.agent_events_repository import agent_events_repository
from app.repositories.findings_repository import findings_repository
from app.repositories.plan_sections_repository import plan_sections_repository
from app.repositories.reports_repository import reports_repository
from app.repositories.research_tasks_repository import research_tasks_repository
from app.repositories.runs_repository import runs_repository
from app.repositories.sources_repository import sources_repository
from app.schemas.common import success_response

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    settings = get_settings()
    supabase = check_supabase_connection(settings)

    runs_table = False
    plan_sections_table = False
    sources_table = False
    findings_table = False
    research_tasks_table = False
    agent_events_table = False
    reports_table = False
    if supabase.get("connected"):
        try:
            runs_table = runs_repository.table_exists()
        except Exception:
            runs_table = False
        try:
            plan_sections_table = plan_sections_repository.table_exists()
        except Exception:
            plan_sections_table = False
        try:
            sources_table = sources_repository.table_exists()
        except Exception:
            sources_table = False
        try:
            findings_table = findings_repository.table_exists()
        except Exception:
            findings_table = False
        try:
            research_tasks_table = research_tasks_repository.table_exists()
        except Exception:
            research_tasks_table = False
        try:
            agent_events_table = agent_events_repository.table_exists()
        except Exception:
            agent_events_table = False
        try:
            reports_table = reports_repository.table_exists()
        except Exception:
            reports_table = False

    supabase = {
        **supabase,
        "runs_table": runs_table,
        "plan_sections_table": plan_sections_table,
        "sources_table": sources_table,
        "findings_table": findings_table,
        "research_tasks_table": research_tasks_table,
        "agent_events_table": agent_events_table,
        "reports_table": reports_table,
        "openrouter_configured": settings.openrouter_configured,
        "search_provider": settings.search_provider,
    }

    overall = "healthy"
    if supabase["configured"] and not supabase["connected"]:
        overall = "degraded"
    elif supabase["connected"] and not runs_table:
        overall = "degraded"
    elif runs_table and not plan_sections_table:
        overall = "degraded"

    return success_response(
        data={
            "status": overall,
            "service": settings.app_name,
            "version": __version__,
            "supabase": supabase,
            "reliability": {
                "tool_timeout_s": settings.research_tool_timeout_s,
                "tool_max_retries": settings.research_tool_max_retries,
                "run_timeout_s": settings.run_timeout_s,
                "run_cost_limit_usd": settings.run_cost_limit_usd,
                "openrouter_max_retries": settings.openrouter_max_retries,
                "research_max_workers": settings.research_max_workers,
            },
        },
        message="Backend is running",
    )
