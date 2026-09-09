"""Supabase repository for reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from postgrest.exceptions import APIError

from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.supabase import get_supabase_client
from app.schemas.report import ReportResponse

logger = get_logger(__name__)
TABLE = "reports"


class ReportsRepository:
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

    def get_for_run(self, run_id: UUID) -> ReportResponse:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .limit(1)
                .execute()
            )
        except APIError as e:
            raise self._map(e, "get") from e
        if not result.data:
            raise NotFoundError(
                "Report not found for this run. Generate a report first.",
                detail={"run_id": str(run_id)},
            )
        return self._to(result.data[0])

    def find_for_run(self, run_id: UUID) -> Optional[ReportResponse]:
        try:
            return self.get_for_run(run_id)
        except NotFoundError:
            return None

    def upsert(self, row: dict[str, Any]) -> ReportResponse:
        payload = {
            **row,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            result = (
                self.client.table(TABLE)
                .upsert(payload, on_conflict="run_id")
                .execute()
            )
        except APIError as e:
            raise self._map(e, "upsert") from e
        if not result.data:
            raise AppError("Failed to save report", status_code=500)
        return self._to(result.data[0])

    def update_for_run(self, run_id: UUID, fields: dict[str, Any]) -> ReportResponse:
        self.get_for_run(run_id)
        payload = {
            **fields,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            result = (
                self.client.table(TABLE)
                .update(payload)
                .eq("run_id", str(run_id))
                .execute()
            )
        except APIError as e:
            raise self._map(e, "update") from e
        if not result.data:
            raise NotFoundError("Report not found for this run")
        return self._to(result.data[0])

    @staticmethod
    def _to(row: dict[str, Any]) -> ReportResponse:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        updated_at = row.get("updated_at") or row["created_at"]
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))

        sections = row.get("sections_json") or []
        if not isinstance(sections, list):
            sections = []
        confidence = row.get("sections_confidence_json")
        unverified = row.get("unverified_claims_json")
        citation_map = row.get("citation_map_json") or {}
        if not isinstance(citation_map, dict):
            citation_map = {}
        conflicts = row.get("conflicts_json")
        verification = row.get("verification_json")
        verified_at = row.get("verified_at")
        if isinstance(verified_at, str):
            verified_at = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))

        return ReportResponse(
            id=UUID(str(row["id"])),
            run_id=UUID(str(row["run_id"])),
            markdown=row.get("markdown") or "",
            sections_json=sections,
            sections_confidence_json=confidence if isinstance(confidence, dict) else None,
            unverified_claims_json=unverified if isinstance(unverified, list) else None,
            citation_map_json=citation_map,
            conflicts_json=conflicts if isinstance(conflicts, list) else None,
            verification_json=verification if isinstance(verification, dict) else None,
            verified_at=verified_at,
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _map(exc: APIError, action: str) -> AppError:
        message = str(getattr(exc, "message", None) or exc)
        code = getattr(exc, "code", None)
        logger.error("Supabase reports.%s failed code=%s message=%s", action, code, message)
        if code == "PGRST205" or "Could not find the table" in message:
            return AppError(
                "reports table is missing. Apply migrations/007_create_reports_and_writer_events.sql",
                status_code=503,
                detail={"code": code, "action": action},
            )
        return AppError("Database operation failed", status_code=502, detail={"code": code})


reports_repository = ReportsRepository()
