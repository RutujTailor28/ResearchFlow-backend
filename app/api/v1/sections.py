"""Plan section CRUD and reorder endpoints (Phase 3)."""

from uuid import UUID

from fastapi import APIRouter, Path

from app.schemas.common import success_response
from app.schemas.section import (
    CreateSectionRequest,
    ReorderSectionsRequest,
    UpdateSectionRequest,
)
from app.services.section_service import section_service

router = APIRouter(prefix="/runs", tags=["sections"])


@router.post("/{run_id}/sections")
def create_section(payload: CreateSectionRequest, run_id: UUID = Path(...)) -> dict:
    section = section_service.create_section(run_id, payload)
    return success_response(
        data=section.model_dump(mode="json"),
        message="Section created",
    )


@router.get("/{run_id}/sections")
def list_sections(run_id: UUID = Path(...)) -> dict:
    result = section_service.list_sections(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Sections retrieved",
    )


@router.put("/{run_id}/sections/reorder")
def reorder_sections(
    payload: ReorderSectionsRequest,
    run_id: UUID = Path(...),
) -> dict:
    result = section_service.reorder_sections(run_id, payload)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Sections reordered",
    )


@router.get("/{run_id}/sections/{section_id}")
def get_section(run_id: UUID = Path(...), section_id: UUID = Path(...)) -> dict:
    section = section_service.get_section(run_id, section_id)
    return success_response(
        data=section.model_dump(mode="json"),
        message="Section retrieved",
    )


@router.put("/{run_id}/sections/{section_id}")
def update_section(
    payload: UpdateSectionRequest,
    run_id: UUID = Path(...),
    section_id: UUID = Path(...),
) -> dict:
    section = section_service.update_section(run_id, section_id, payload)
    return success_response(
        data=section.model_dump(mode="json"),
        message="Section updated",
    )


@router.delete("/{run_id}/sections/{section_id}")
def delete_section(run_id: UUID = Path(...), section_id: UUID = Path(...)) -> dict:
    section_service.delete_section(run_id, section_id)
    return success_response(
        data={"id": str(section_id), "deleted": True},
        message="Section deleted",
    )
