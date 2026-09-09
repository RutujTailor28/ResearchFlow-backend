"""Report generation endpoints (Phase 7)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Path

from app.schemas.common import success_response
from app.schemas.report import RegenerateSectionRequest, UpdateReportRequest
from app.services.report_service import report_service

router = APIRouter(prefix="/runs", tags=["reports"])


@router.post("/{run_id}/report/generate")
def generate_report(run_id: UUID = Path(...)) -> dict:
    result = report_service.generate_report(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Report generated",
    )


@router.get("/{run_id}/report")
def get_report(run_id: UUID = Path(...)) -> dict:
    report = report_service.get_report(run_id)
    return success_response(
        data=report.model_dump(mode="json"),
        message="Report retrieved",
    )


@router.put("/{run_id}/report")
def update_report(payload: UpdateReportRequest, run_id: UUID = Path(...)) -> dict:
    report = report_service.update_report(run_id, payload)
    return success_response(
        data=report.model_dump(mode="json"),
        message="Report updated",
    )


@router.post("/{run_id}/sections/{section_id}/generate")
def generate_section(
    run_id: UUID = Path(...),
    section_id: UUID = Path(...),
) -> dict:
    report = report_service.generate_section(run_id, section_id)
    return success_response(
        data=report.model_dump(mode="json"),
        message="Section generated",
    )


@router.post("/{run_id}/sections/{section_id}/regenerate")
def regenerate_section(
    run_id: UUID = Path(...),
    section_id: UUID = Path(...),
    payload: Optional[RegenerateSectionRequest] = Body(default=None),
) -> dict:
    report = report_service.regenerate_section(
        run_id, section_id, payload or RegenerateSectionRequest()
    )
    return success_response(
        data=report.model_dump(mode="json"),
        message="Section regenerated",
    )
