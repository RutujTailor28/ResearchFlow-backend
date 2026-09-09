"""Run-scoped multi-agent research orchestration endpoints (Phase 5)."""

from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.schemas.common import success_response
from app.services.research_orchestration_service import research_orchestration_service

router = APIRouter(prefix="/runs", tags=["run-research"])


@router.post("/{run_id}/research/start")
def start_research(
    run_id: UUID = Path(...),
    sync: bool = Query(
        default=False,
        description="If true, run research inline (useful for tests).",
    ),
) -> dict:
    result = research_orchestration_service.start_research(run_id, sync=sync)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Research started",
    )


@router.get("/{run_id}/research/status")
def research_status(run_id: UUID = Path(...)) -> dict:
    result = research_orchestration_service.get_status(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Research status retrieved",
    )


@router.get("/{run_id}/research/results")
def research_results(run_id: UUID = Path(...)) -> dict:
    result = research_orchestration_service.get_results(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Research results retrieved",
    )


@router.post("/{run_id}/research/retry")
def retry_research(
    run_id: UUID = Path(...),
    sync: bool = Query(default=False),
) -> dict:
    result = research_orchestration_service.retry_research(run_id, sync=sync)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Research retry started",
    )
