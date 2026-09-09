"""Supabase repository for findings."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from postgrest.exceptions import APIError

from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.supabase import get_supabase_client
from app.schemas.finding import FindingResponse

logger = get_logger(__name__)
TABLE = "findings"


class FindingsRepository:
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
            raise self._map(exc, "probe") from exc

    def list_for_run(self, run_id: UUID) -> list[FindingResponse]:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .order("created_at", desc=False)
                .execute()
            )
        except APIError as e:
            raise self._map(e, "list") from e
        return [self._to(r) for r in (result.data or [])]

    def list_for_section(self, run_id: UUID, section_id: UUID) -> list[FindingResponse]:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .eq("section_id", str(section_id))
                .order("created_at", desc=False)
                .execute()
            )
        except APIError as e:
            raise self._map(e, "list_section") from e
        return [self._to(r) for r in (result.data or [])]

    def get_for_run(self, run_id: UUID, finding_id: UUID) -> FindingResponse:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .eq("id", str(finding_id))
                .limit(1)
                .execute()
            )
        except APIError as e:
            raise self._map(e, "get") from e
        if not result.data:
            raise NotFoundError(
                "Finding not found for this run",
                detail={"run_id": str(run_id), "finding_id": str(finding_id)},
            )
        return self._to(result.data[0])

    def create(self, row: dict[str, Any]) -> FindingResponse:
        try:
            result = self.client.table(TABLE).insert(row).execute()
        except APIError as e:
            raise self._map(e, "create") from e
        if not result.data:
            raise AppError("Failed to create finding", status_code=500)
        return self._to(result.data[0])

    def update(self, run_id: UUID, finding_id: UUID, fields: dict[str, Any]) -> FindingResponse:
        self.get_for_run(run_id, finding_id)
        try:
            result = (
                self.client.table(TABLE)
                .update(fields)
                .eq("run_id", str(run_id))
                .eq("id", str(finding_id))
                .execute()
            )
        except APIError as e:
            raise self._map(e, "update") from e
        if not result.data:
            raise NotFoundError("Finding not found for this run")
        return self._to(result.data[0])

    def delete(self, run_id: UUID, finding_id: UUID) -> None:
        self.get_for_run(run_id, finding_id)
        try:
            self.client.table(TABLE).delete().eq("run_id", str(run_id)).eq(
                "id", str(finding_id)
            ).execute()
        except APIError as e:
            raise self._map(e, "delete") from e

    @staticmethod
    def _to(row: dict[str, Any]) -> FindingResponse:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return FindingResponse(
            id=UUID(str(row["id"])),
            run_id=UUID(str(row["run_id"])),
            section_id=UUID(str(row["section_id"])),
            claim=row["claim"],
            source_id=UUID(str(row["source_id"])) if row.get("source_id") else None,
            confidence=float(row.get("confidence") or 0.5),
            is_gap=bool(row.get("is_gap") or False),
            created_at=created_at,
        )

    @staticmethod
    def _map(exc: APIError, action: str) -> AppError:
        message = str(getattr(exc, "message", None) or exc)
        code = getattr(exc, "code", None)
        logger.error("Supabase findings.%s failed code=%s message=%s", action, code, message)
        if code == "PGRST205" or "Could not find the table" in message:
            return AppError(
                "findings table is missing. Apply migrations/005_create_findings_and_research_tasks.sql",
                status_code=503,
                detail={"code": code, "action": action},
            )
        return AppError("Database operation failed", status_code=502, detail={"code": code})


findings_repository = FindingsRepository()
