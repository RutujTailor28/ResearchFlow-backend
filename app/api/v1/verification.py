"""Verification endpoints (Phase 8)."""

from uuid import UUID

from fastapi import APIRouter, Path

from app.schemas.common import success_response
from app.services.verification_service import verification_service

router = APIRouter(prefix="/runs", tags=["verification"])


@router.post("/{run_id}/verify")
def verify_report(run_id: UUID = Path(...)) -> dict:
    result = verification_service.verify_run(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Verification completed",
    )


@router.get("/{run_id}/verification")
def get_verification(run_id: UUID = Path(...)) -> dict:
    result = verification_service.get_verification(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Verification retrieved",
    )


@router.get("/{run_id}/conflicts")
def get_conflicts(run_id: UUID = Path(...)) -> dict:
    result = verification_service.get_conflicts(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Conflicts retrieved",
    )
