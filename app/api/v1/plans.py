"""Plan endpoints nested under runs (Phase 2)."""

from uuid import UUID

from fastapi import APIRouter, Path

from app.schemas.common import success_response
from app.schemas.plan import ImprovePlanRequest, UpdatePlanRequest
from app.services.planner_service import planner_service

router = APIRouter(prefix="/runs", tags=["plans"])


@router.get("/{run_id}/plan")
def get_plan(run_id: UUID = Path(...)) -> dict:
    detail = planner_service.get_plan(run_id)
    return success_response(
        data=detail.model_dump(mode="json"),
        message="Plan retrieved",
    )


@router.put("/{run_id}/plan")
def update_plan(payload: UpdatePlanRequest, run_id: UUID = Path(...)) -> dict:
    result = planner_service.update_plan(run_id, payload)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Plan updated",
    )


@router.post("/{run_id}/plan/generate")
def generate_plan(run_id: UUID = Path(...)) -> dict:
    result = planner_service.generate_plan(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="AI research plan generated",
    )


@router.post("/{run_id}/plan/improve")
def improve_plan(payload: ImprovePlanRequest, run_id: UUID = Path(...)) -> dict:
    result = planner_service.improve_plan(run_id, payload)
    return success_response(
        data=result.model_dump(mode="json"),
        message="AI research plan improved",
    )


@router.post("/{run_id}/plan/approve")
def approve_plan(run_id: UUID = Path(...)) -> dict:
    detail = planner_service.approve_plan(run_id)
    return success_response(
        data=detail.model_dump(mode="json"),
        message="Plan approved",
    )
