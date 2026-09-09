"""Finding CRUD service (Phase 5)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.repositories.findings_repository import FindingsRepository, findings_repository
from app.repositories.plan_sections_repository import (
    PlanSectionsRepository,
    plan_sections_repository,
)
from app.repositories.sources_repository import SourcesRepository, sources_repository
from app.schemas.finding import (
    CreateFindingRequest,
    FindingListResponse,
    FindingResponse,
    UpdateFindingRequest,
)
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)


class FindingService:
    def __init__(
        self,
        runs: RunService | None = None,
        findings: FindingsRepository | None = None,
        sections: PlanSectionsRepository | None = None,
        sources: SourcesRepository | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._findings = findings or findings_repository
        self._sections = sections or plan_sections_repository
        self._sources = sources or sources_repository

    def list_findings(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> FindingListResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        items = self._findings.list_for_run(run.id)
        return FindingListResponse(items=items, total=len(items))

    def list_section_findings(
        self,
        run_id: UUID,
        section_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> FindingListResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        self._sections.get_for_run(run.id, section_id)
        items = self._findings.list_for_section(run.id, section_id)
        return FindingListResponse(items=items, total=len(items))

    def get_finding(
        self,
        run_id: UUID,
        finding_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> FindingResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        return self._findings.get_for_run(run.id, finding_id)

    def create_finding(
        self,
        run_id: UUID,
        payload: CreateFindingRequest,
        *,
        user_id: Optional[UUID] = None,
    ) -> FindingResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        self._sections.get_for_run(run.id, payload.section_id)
        if payload.source_id is not None:
            self._sources.get_for_run(run.id, payload.source_id)
        if payload.is_gap is False and not payload.claim.strip():
            raise ValidationAppError("Non-gap findings require a claim")

        row = {
            "id": str(uuid4()),
            "run_id": str(run.id),
            "section_id": str(payload.section_id),
            "claim": payload.claim,
            "source_id": str(payload.source_id) if payload.source_id else None,
            "confidence": payload.confidence,
            "is_gap": payload.is_gap,
        }
        saved = self._findings.create(row)
        logger.info("Created finding id=%s run_id=%s", saved.id, run.id)
        return saved

    def update_finding(
        self,
        run_id: UUID,
        finding_id: UUID,
        payload: UpdateFindingRequest,
        *,
        user_id: Optional[UUID] = None,
    ) -> FindingResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        fields: dict = {}
        if payload.claim is not None:
            fields["claim"] = payload.claim
        if payload.confidence is not None:
            fields["confidence"] = payload.confidence
        if payload.is_gap is not None:
            fields["is_gap"] = payload.is_gap
        if payload.clear_source:
            fields["source_id"] = None
        elif payload.source_id is not None:
            self._sources.get_for_run(run.id, payload.source_id)
            fields["source_id"] = str(payload.source_id)
        if not fields:
            raise ValidationAppError("No fields to update")
        return self._findings.update(run.id, finding_id, fields)

    def delete_finding(
        self,
        run_id: UUID,
        finding_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> None:
        run = self._authorized_run(run_id, user_id=user_id)
        self._findings.delete(run.id, finding_id)
        logger.info("Deleted finding id=%s run_id=%s", finding_id, run.id)

    def _authorized_run(self, run_id: UUID, *, user_id: Optional[UUID]):
        run = self._runs.get_run(run_id)
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run


finding_service = FindingService()
