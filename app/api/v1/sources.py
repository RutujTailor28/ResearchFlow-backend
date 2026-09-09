"""Run-scoped source management endpoints (Phase 4)."""

from uuid import UUID

from fastapi import APIRouter, Path

from app.schemas.common import success_response
from app.schemas.source import CreateSourceRequest
from app.services.source_service import source_service

router = APIRouter(prefix="/runs", tags=["sources"])


@router.get("/{run_id}/sources")
def list_sources(run_id: UUID = Path(...)) -> dict:
    result = source_service.list_sources(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Sources retrieved",
    )


@router.get("/{run_id}/sources/{source_id}")
def get_source(run_id: UUID = Path(...), source_id: UUID = Path(...)) -> dict:
    source = source_service.get_source(run_id, source_id)
    return success_response(
        data=source.model_dump(mode="json"),
        message="Source retrieved",
    )


@router.post("/{run_id}/sources")
def create_source(payload: CreateSourceRequest, run_id: UUID = Path(...)) -> dict:
    result = source_service.add_source(run_id, payload)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Source added",
    )


@router.delete("/{run_id}/sources/{source_id}")
def delete_source(run_id: UUID = Path(...), source_id: UUID = Path(...)) -> dict:
    source_service.delete_source(run_id, source_id)
    return success_response(
        data={"id": str(source_id), "deleted": True},
        message="Source deleted",
    )
