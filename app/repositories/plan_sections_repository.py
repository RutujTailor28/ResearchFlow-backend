"""Supabase repository for plan_sections."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from postgrest.exceptions import APIError

from app.core.exceptions import AppError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.core.supabase import get_supabase_client
from app.schemas.plan import PlanSection, ResearchPlan, SectionStatus
from app.schemas.section import SectionResponse

logger = get_logger(__name__)

TABLE = "plan_sections"
# Temporary offset used during reorder to avoid UNIQUE(run_id, order_index) collisions.
_REORDER_OFFSET = 100_000


class PlanSectionsRepository:
    def __init__(self, client=None) -> None:
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = get_supabase_client("service")
        return self._client

    def table_exists(self) -> bool:
        try:
            self.client.table(TABLE).select("id").limit(1).execute()
            return True
        except APIError as exc:
            message = str(getattr(exc, "message", exc))
            if "Could not find the table" in message or getattr(exc, "code", None) == "PGRST205":
                return False
            raise self._map_api_error(exc, action="probe") from exc

    def list_records_for_run(self, run_id: UUID) -> list[SectionResponse]:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .order("order_index", desc=False)
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="list") from exc
        return [self._to_record(row) for row in (result.data or [])]

    def list_for_run(self, run_id: UUID) -> list[PlanSection]:
        return [self._record_to_plan_section(r) for r in self.list_records_for_run(run_id)]

    def get_for_run(self, run_id: UUID, section_id: UUID) -> SectionResponse:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("id", str(section_id))
                .eq("run_id", str(run_id))
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="get") from exc
        if not result.data:
            raise NotFoundError(
                "Section not found for this run",
                detail={"run_id": str(run_id), "section_id": str(section_id)},
            )
        return self._to_record(result.data[0])

    def create(
        self,
        *,
        run_id: UUID,
        title: str,
        description: str,
        sub_questions: list[str],
        status: SectionStatus,
        order_index: int,
    ) -> SectionResponse:
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": str(uuid4()),
            "run_id": str(run_id),
            "order_index": order_index,
            "title": title,
            "description": description,
            "sub_questions": sub_questions,
            "status": status.value,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = self.client.table(TABLE).insert(row).execute()
        except APIError as exc:
            raise self._map_api_error(exc, action="create") from exc
        if not result.data:
            raise AppError("Failed to create section", status_code=500)
        return self._to_record(result.data[0])

    def update_fields(
        self,
        *,
        run_id: UUID,
        section_id: UUID,
        fields: dict[str, Any],
    ) -> SectionResponse:
        # Ensure ownership before update.
        self.get_for_run(run_id, section_id)
        payload = {
            **fields,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            result = (
                self.client.table(TABLE)
                .update(payload)
                .eq("id", str(section_id))
                .eq("run_id", str(run_id))
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="update") from exc
        if not result.data:
            raise NotFoundError(
                "Section not found for this run",
                detail={"run_id": str(run_id), "section_id": str(section_id)},
            )
        return self._to_record(result.data[0])

    def delete(self, *, run_id: UUID, section_id: UUID) -> None:
        self.get_for_run(run_id, section_id)
        try:
            self.client.table(TABLE).delete().eq("id", str(section_id)).eq(
                "run_id", str(run_id)
            ).execute()
        except APIError as exc:
            raise self._map_api_error(exc, action="delete") from exc

    def replace_for_run(self, run_id: UUID, plan: ResearchPlan) -> list[PlanSection]:
        """Delete existing sections and insert the new plan sections."""
        self.delete_for_run(run_id)
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for section in plan.sections:
            rows.append(
                {
                    "id": str(uuid4()),
                    "run_id": str(run_id),
                    "order_index": section.order_index,
                    "title": section.title,
                    "description": section.description,
                    "sub_questions": section.sub_questions,
                    "status": section.status.value,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        if not rows:
            return []
        try:
            result = self.client.table(TABLE).insert(rows).execute()
        except APIError as exc:
            raise self._map_api_error(exc, action="insert") from exc

        saved = [self._to_record(row) for row in (result.data or [])]
        saved.sort(key=lambda s: s.order_index)
        return [self._record_to_plan_section(r) for r in saved]

    def delete_for_run(self, run_id: UUID) -> None:
        try:
            self.client.table(TABLE).delete().eq("run_id", str(run_id)).execute()
        except APIError as exc:
            raise self._map_api_error(exc, action="delete_for_run") from exc

    def max_order_index(self, run_id: UUID) -> int:
        records = self.list_records_for_run(run_id)
        if not records:
            return 0
        return max(r.order_index for r in records)

    def reindex_run(self, run_id: UUID) -> list[SectionResponse]:
        """Normalize order_index to 1..N for a run (safe two-phase update)."""
        records = self.list_records_for_run(run_id)
        if not records:
            return []
        self._apply_order(run_id, [r.id for r in records])
        return self.list_records_for_run(run_id)

    def reorder(self, run_id: UUID, section_ids: list[UUID]) -> list[SectionResponse]:
        existing = self.list_records_for_run(run_id)
        existing_ids = {r.id for r in existing}

        if len(section_ids) != len(existing_ids):
            raise ValidationAppError(
                "section_ids must include every section for this run exactly once",
                detail={
                    "expected_count": len(existing_ids),
                    "provided_count": len(section_ids),
                },
            )

        missing = [str(sid) for sid in section_ids if sid not in existing_ids]
        if missing:
            raise ValidationAppError(
                "One or more section_ids do not belong to this run",
                detail={"invalid_section_ids": missing},
            )

        self._apply_order(run_id, section_ids)
        return self.list_records_for_run(run_id)

    def _apply_order(self, run_id: UUID, section_ids: list[UUID]) -> None:
        """Two-phase order update to satisfy UNIQUE(run_id, order_index)."""
        now = datetime.now(timezone.utc).isoformat()
        # Phase 1: move to temporary indexes
        for index, section_id in enumerate(section_ids):
            try:
                self.client.table(TABLE).update(
                    {
                        "order_index": _REORDER_OFFSET + index + 1,
                        "updated_at": now,
                    }
                ).eq("id", str(section_id)).eq("run_id", str(run_id)).execute()
            except APIError as exc:
                raise self._map_api_error(exc, action="reorder_temp") from exc

        # Phase 2: assign final 1..N indexes
        for index, section_id in enumerate(section_ids, start=1):
            try:
                self.client.table(TABLE).update(
                    {
                        "order_index": index,
                        "updated_at": now,
                    }
                ).eq("id", str(section_id)).eq("run_id", str(run_id)).execute()
            except APIError as exc:
                raise self._map_api_error(exc, action="reorder_final") from exc

    def shift_orders_up(self, run_id: UUID, from_index: int) -> None:
        """Increment order_index for sections at/after from_index (two-phase)."""
        records = [
            r for r in self.list_records_for_run(run_id) if r.order_index >= from_index
        ]
        if not records:
            return
        now = datetime.now(timezone.utc).isoformat()
        for r in records:
            try:
                self.client.table(TABLE).update(
                    {
                        "order_index": _REORDER_OFFSET + r.order_index,
                        "updated_at": now,
                    }
                ).eq("id", str(r.id)).eq("run_id", str(run_id)).execute()
            except APIError as exc:
                raise self._map_api_error(exc, action="shift_temp") from exc
        for r in records:
            try:
                self.client.table(TABLE).update(
                    {
                        "order_index": r.order_index + 1,
                        "updated_at": now,
                    }
                ).eq("id", str(r.id)).eq("run_id", str(run_id)).execute()
            except APIError as exc:
                raise self._map_api_error(exc, action="shift_final") from exc

    @staticmethod
    def _record_to_plan_section(record: SectionResponse) -> PlanSection:
        return PlanSection(
            id=str(record.id),
            order_index=record.order_index,
            title=record.title,
            description=record.description,
            sub_questions=list(record.sub_questions),
            status=record.status,
        )

    @staticmethod
    def _to_record(row: dict[str, Any]) -> SectionResponse:
        subs = row.get("sub_questions") or []
        if isinstance(subs, str):
            import json

            subs = json.loads(subs)

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        updated_at = row.get("updated_at") or row["created_at"]
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))

        status_raw = row.get("status") or "pending"
        try:
            status = SectionStatus(status_raw)
        except ValueError:
            status = SectionStatus.pending

        return SectionResponse(
            id=UUID(str(row["id"])),
            run_id=UUID(str(row["run_id"])),
            order_index=int(row["order_index"]),
            title=row["title"],
            description=row.get("description") or "",
            sub_questions=list(subs),
            status=status,
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _map_api_error(exc: APIError, *, action: str) -> AppError:
        message = str(getattr(exc, "message", None) or exc)
        code = getattr(exc, "code", None)
        logger.error(
            "Supabase plan_sections.%s failed code=%s message=%s",
            action,
            code,
            message,
        )
        if code == "PGRST205" or "Could not find the table" in message:
            return AppError(
                "plan_sections table is missing. Apply migrations/002_create_plan_sections.sql",
                status_code=503,
                detail={"code": code, "action": action},
            )
        return AppError(
            "Database operation failed",
            status_code=502,
            detail={"code": code, "action": action},
        )


plan_sections_repository = PlanSectionsRepository()
