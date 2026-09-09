"""Usage, cost, history, and export endpoints (Phase 9)."""

from uuid import UUID

from fastapi import APIRouter, Path, Query
from fastapi.responses import Response

from app.schemas.common import success_response
from app.schemas.usage import ExportFormat
from app.services.export_service import export_service
from app.services.usage_service import usage_service

router = APIRouter(prefix="/runs", tags=["usage-export-history"])


@router.get("/{run_id}/usage")
def get_usage(run_id: UUID = Path(...)) -> dict:
    result = usage_service.get_usage(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Usage retrieved",
    )


@router.get("/{run_id}/cost")
def get_cost(run_id: UUID = Path(...)) -> dict:
    result = usage_service.get_cost(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Cost retrieved",
    )


@router.get("/{run_id}/history")
def get_history(run_id: UUID = Path(...)) -> dict:
    """Run summary + timeline replay from stored agent_events."""
    result = usage_service.get_history(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="History retrieved",
    )


@router.get("/{run_id}/export")
def export_report(
    run_id: UUID = Path(...),
    format: ExportFormat = Query(default=ExportFormat.md, alias="format"),
) -> Response:
    return export_service.export(run_id, format=format)
