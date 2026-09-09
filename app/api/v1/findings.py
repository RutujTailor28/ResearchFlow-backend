"""Finding CRUD endpoints (Phase 5)."""

from uuid import UUID

from fastapi import APIRouter, Path

from app.schemas.common import success_response
from app.schemas.finding import CreateFindingRequest, UpdateFindingRequest
from app.services.finding_service import finding_service

router = APIRouter(prefix="/runs", tags=["findings"])


@router.get("/{run_id}/findings")
def list_findings(run_id: UUID = Path(...)) -> dict:
    result = finding_service.list_findings(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Findings retrieved",
    )


@router.get("/{run_id}/sections/{section_id}/findings")
def list_section_findings(
    run_id: UUID = Path(...),
    section_id: UUID = Path(...),
) -> dict:
    result = finding_service.list_section_findings(run_id, section_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Section findings retrieved",
    )


@router.get("/{run_id}/findings/{finding_id}")
def get_finding(
    run_id: UUID = Path(...),
    finding_id: UUID = Path(...),
) -> dict:
    finding = finding_service.get_finding(run_id, finding_id)
    return success_response(
        data=finding.model_dump(mode="json"),
        message="Finding retrieved",
    )


@router.post("/{run_id}/findings")
def create_finding(
    payload: CreateFindingRequest,
    run_id: UUID = Path(...),
) -> dict:
    finding = finding_service.create_finding(run_id, payload)
    return success_response(
        data=finding.model_dump(mode="json"),
        message="Finding created",
    )


@router.put("/{run_id}/findings/{finding_id}")
def update_finding(
    payload: UpdateFindingRequest,
    run_id: UUID = Path(...),
    finding_id: UUID = Path(...),
) -> dict:
    finding = finding_service.update_finding(run_id, finding_id, payload)
    return success_response(
        data=finding.model_dump(mode="json"),
        message="Finding updated",
    )


@router.delete("/{run_id}/findings/{finding_id}")
def delete_finding(
    run_id: UUID = Path(...),
    finding_id: UUID = Path(...),
) -> dict:
    finding_service.delete_finding(run_id, finding_id)
    return success_response(
        data={"id": str(finding_id), "deleted": True},
        message="Finding deleted",
    )
