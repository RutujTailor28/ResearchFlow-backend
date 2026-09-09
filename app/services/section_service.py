"""Phase 3 section management service."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.core.exceptions import ConflictError, ForbiddenError, ValidationAppError
from app.core.logging import get_logger
from app.repositories.plan_sections_repository import (
    PlanSectionsRepository,
    plan_sections_repository,
)
from app.schemas.run import RunResponse, RunStatus
from app.schemas.section import (
    CreateSectionRequest,
    ReorderSectionsRequest,
    SectionListResponse,
    SectionResponse,
    UpdateSectionRequest,
)
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)

# Runs that should not allow structural plan edits.
LOCKED_STATUSES = {
    RunStatus.running,
    RunStatus.completed,
    RunStatus.cancelled,
}


class SectionService:
    """
    Section CRUD / reorder.

    Auth-ready: pass `user_id` later to enforce run ownership without
    changing route/service shape.
    """

    def __init__(
        self,
        runs: RunService | None = None,
        sections: PlanSectionsRepository | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._sections = sections or plan_sections_repository

    def list_sections(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> SectionListResponse:
        run = self._get_authorized_run(run_id, user_id=user_id)
        items = self._sections.list_records_for_run(run.id)
        return SectionListResponse(items=items, total=len(items))

    def get_section(
        self,
        run_id: UUID,
        section_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> SectionResponse:
        run = self._get_authorized_run(run_id, user_id=user_id)
        return self._sections.get_for_run(run.id, section_id)

    def create_section(
        self,
        run_id: UUID,
        payload: CreateSectionRequest,
        *,
        user_id: Optional[UUID] = None,
    ) -> SectionResponse:
        run = self._get_authorized_run(run_id, user_id=user_id)
        self._ensure_editable(run)

        current = self._sections.list_records_for_run(run.id)
        next_index = len(current) + 1
        target_index = payload.order_index or next_index
        if target_index < 1 or target_index > next_index:
            raise ValidationAppError(
                "order_index is out of range",
                detail={"order_index": target_index, "max_allowed": next_index},
            )

        if target_index <= len(current):
            self._sections.shift_orders_up(run.id, target_index)

        created = self._sections.create(
            run_id=run.id,
            title=payload.title,
            description=payload.description,
            sub_questions=payload.sub_questions,
            status=payload.status,
            order_index=target_index,
        )
        self._sync_plan_json(run.id)
        logger.info(
            "Created section id=%s run_id=%s order=%s",
            created.id,
            run.id,
            created.order_index,
        )
        return created

    def update_section(
        self,
        run_id: UUID,
        section_id: UUID,
        payload: UpdateSectionRequest,
        *,
        user_id: Optional[UUID] = None,
    ) -> SectionResponse:
        run = self._get_authorized_run(run_id, user_id=user_id)
        self._ensure_editable(run)

        fields: dict = {}
        if payload.title is not None:
            fields["title"] = payload.title
        if payload.description is not None:
            fields["description"] = payload.description
        if payload.sub_questions is not None:
            fields["sub_questions"] = payload.sub_questions
        if payload.status is not None:
            fields["status"] = payload.status.value

        updated = self._sections.update_fields(
            run_id=run.id,
            section_id=section_id,
            fields=fields,
        )
        self._sync_plan_json(run.id)
        logger.info("Updated section id=%s run_id=%s", section_id, run.id)
        return updated

    def delete_section(
        self,
        run_id: UUID,
        section_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> None:
        run = self._get_authorized_run(run_id, user_id=user_id)
        self._ensure_editable(run)
        self._sections.delete(run_id=run.id, section_id=section_id)
        self._sections.reindex_run(run.id)
        self._sync_plan_json(run.id)
        logger.info("Deleted section id=%s run_id=%s", section_id, run.id)

    def reorder_sections(
        self,
        run_id: UUID,
        payload: ReorderSectionsRequest,
        *,
        user_id: Optional[UUID] = None,
    ) -> SectionListResponse:
        run = self._get_authorized_run(run_id, user_id=user_id)
        self._ensure_editable(run)
        items = self._sections.reorder(run.id, list(payload.section_ids))
        self._sync_plan_json(run.id)
        logger.info("Reordered %s sections for run_id=%s", len(items), run.id)
        return SectionListResponse(items=items, total=len(items))

    def _get_authorized_run(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID],
    ) -> RunResponse:
        run = self._runs.get_run(run_id)
        # Future auth hook: when user_id is provided, enforce ownership.
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run

    @staticmethod
    def _ensure_editable(run: RunResponse) -> None:
        if run.status in LOCKED_STATUSES:
            raise ConflictError(
                f"Cannot modify sections when run status is '{run.status.value}'",
                detail={"run_id": str(run.id), "status": run.status.value},
            )

    def _sync_plan_json(self, run_id: UUID) -> None:
        """Keep runs.plan_json in sync with plan_sections after structural edits."""
        run = self._runs.get_run(run_id)
        sections = self._sections.list_for_run(run_id)
        title = run.topic
        summary = ""
        if run.plan_json and isinstance(run.plan_json, dict):
            title = str(run.plan_json.get("title") or title)
            summary = str(run.plan_json.get("summary") or "")

        plan_json = {
            "title": title,
            "summary": summary,
            "sections": [s.model_dump(mode="json") for s in sections],
        }
        self._runs.update_run_status(run_id, run.status, plan_json=plan_json)


section_service = SectionService()
