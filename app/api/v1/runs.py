"""Research run endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from app.schemas.common import success_response
from app.schemas.run import CreateRunRequest, RunStatus
from app.services.run_service import run_service

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post(
    "",
    summary="Create research run",
    description=(
        "Create a new research run in `plan_pending` status. "
        "Optional `cost_limit_usd` overrides the global `RUN_COST_LIMIT_USD` ceiling."
    ),
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Run created"},
        422: {"description": "Validation failed"},
    },
)
def create_run(payload: CreateRunRequest) -> dict:
    """
    **Example body**
    ```json
    {
      "topic": "Future of AI in healthcare diagnostics",
      "depth": "standard",
      "audience": "technical",
      "tone": "professional",
      "target_length": 3000,
      "source_types": ["official", "research", "news"],
      "cost_limit_usd": 1.0
    }
    ```
    """
    run = run_service.create_run(payload)
    return success_response(data=run.model_dump(mode="json"), message="Run created")


@router.get(
    "",
    summary="List runs (history)",
    description=(
        "List research runs with history fields: topic, status, duration, cost, "
        "source_count, created_at, finished_at."
    ),
)
def list_runs(
    status: Optional[RunStatus] = Query(
        default=None, description="Filter by run status"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    result = run_service.list_runs(status=status, limit=limit, offset=offset)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Runs retrieved",
    )


@router.get(
    "/{run_id}/status",
    summary="Get run status",
    description="Lightweight status probe including cancel_requested and stop_reason.",
)
def get_run_status(run_id: UUID = Path(..., description="Run UUID")) -> dict:
    status_payload = run_service.get_status(run_id)
    return success_response(
        data=status_payload.model_dump(mode="json"),
        message="Run status retrieved",
    )


@router.get(
    "/{run_id}",
    summary="Get run by ID",
    responses={404: {"description": "Run not found"}},
)
def get_run(run_id: UUID = Path(..., description="Run UUID")) -> dict:
    run = run_service.get_run(run_id)
    return success_response(data=run.model_dump(mode="json"), message="Run retrieved")


@router.post(
    "/{run_id}/cancel",
    summary="Cancel run (cooperative)",
    description=(
        "Sets `cancel_requested=true` immediately and marks the run `cancelled`. "
        "Active workers check the flag between steps, stop safely, and preserve "
        "completed findings. Cancellation completes quickly even if workers are mid-tool."
    ),
    responses={
        200: {"description": "Cancel accepted; workers will stop"},
        409: {"description": "Run already completed or cancelled"},
        404: {"description": "Run not found"},
    },
)
def cancel_run(run_id: UUID = Path(..., description="Run UUID")) -> dict:
    run = run_service.cancel_run(run_id)
    return success_response(
        data=run.model_dump(mode="json"),
        message="Run cancelled",
    )


@router.delete(
    "/{run_id}",
    summary="Delete run",
    responses={404: {"description": "Run not found"}},
)
def delete_run(run_id: UUID = Path(..., description="Run UUID")) -> dict:
    run_service.delete_run(run_id)
    return success_response(
        data={"id": str(run_id), "deleted": True},
        message="Run deleted",
    )
