"""Supabase-backed repository for research runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from postgrest.exceptions import APIError

from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.supabase import get_supabase_client
from app.schemas.run import ResearchDepth, RunResponse, RunStatus

logger = get_logger(__name__)

TABLE = "runs"


class RunsRepository:
    """Persistence for runs via Supabase PostgREST."""

    def __init__(self, client=None) -> None:
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = get_supabase_client("service")
        return self._client

    def create(self, row: dict[str, Any]) -> RunResponse:
        try:
            result = self.client.table(TABLE).insert(row).execute()
        except APIError as exc:
            raise self._map_api_error(exc, action="create") from exc
        if not result.data:
            raise AppError("Failed to create run", status_code=500)
        return self._to_response(result.data[0])

    def get_by_id(self, run_id: UUID) -> Optional[RunResponse]:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("id", str(run_id))
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="get") from exc
        if not result.data:
            return None
        return self._to_response(result.data[0])

    def list(
        self,
        *,
        status: Optional[RunStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RunResponse], int]:
        try:
            count_q = self.client.table(TABLE).select("id", count="exact")
            if status is not None:
                count_q = count_q.eq("status", status.value)
            count_result = count_q.execute()
            total = count_result.count if count_result.count is not None else 0

            query = (
                self.client.table(TABLE)
                .select("*")
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
            )
            if status is not None:
                query = query.eq("status", status.value)
            result = query.execute()
        except APIError as exc:
            raise self._map_api_error(exc, action="list") from exc

        items = [self._to_response(row) for row in (result.data or [])]
        return items, int(total)

    def update(self, run_id: UUID, fields: dict[str, Any]) -> RunResponse:
        try:
            result = (
                self.client.table(TABLE)
                .update(fields)
                .eq("id", str(run_id))
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="update") from exc
        if not result.data:
            raise NotFoundError("Run not found", detail={"run_id": str(run_id)})
        return self._to_response(result.data[0])

    def delete(self, run_id: UUID) -> None:
        existing = self.get_by_id(run_id)
        if existing is None:
            raise NotFoundError("Run not found", detail={"run_id": str(run_id)})
        try:
            self.client.table(TABLE).delete().eq("id", str(run_id)).execute()
        except APIError as exc:
            raise self._map_api_error(exc, action="delete") from exc

    def table_exists(self) -> bool:
        try:
            self.client.table(TABLE).select("id").limit(1).execute()
            return True
        except APIError as exc:
            message = str(getattr(exc, "message", exc))
            if "Could not find the table" in message or getattr(exc, "code", None) == "PGRST205":
                return False
            raise self._map_api_error(exc, action="probe") from exc

    @staticmethod
    def _map_api_error(exc: APIError, *, action: str) -> AppError:
        message = str(getattr(exc, "message", None) or exc)
        code = getattr(exc, "code", None)
        logger.error("Supabase runs.%s failed code=%s message=%s", action, code, message)
        if code == "PGRST205" or "Could not find the table" in message:
            return AppError(
                "Runs table is missing. Apply migrations/001_create_runs.sql",
                status_code=503,
                detail={"code": code, "action": action},
            )
        return AppError(
            "Database operation failed",
            status_code=502,
            detail={"code": code, "action": action},
        )

    @staticmethod
    def _to_response(row: dict[str, Any]) -> RunResponse:
        source_types = row.get("source_types") or []
        if isinstance(source_types, str):
            import json

            source_types = json.loads(source_types)

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))

        finished_at = row.get("finished_at")
        if isinstance(finished_at, str):
            finished_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))

        return RunResponse(
            id=UUID(str(row["id"])),
            user_id=UUID(str(row["user_id"])) if row.get("user_id") else None,
            topic=row["topic"],
            depth=ResearchDepth(row["depth"]),
            audience=row["audience"],
            tone=row["tone"],
            target_length=int(row["target_length"]),
            source_types=list(source_types),
            status=RunStatus(row["status"]),
            plan_json=row.get("plan_json"),
            cost_usd=float(row.get("cost_usd") or 0),
            duration_ms=row.get("duration_ms"),
            cancel_requested=bool(row.get("cancel_requested") or False),
            cost_limit_usd=(
                float(row["cost_limit_usd"])
                if row.get("cost_limit_usd") is not None
                else None
            ),
            started_at=_parse_dt(row.get("started_at")),
            stop_reason=row.get("stop_reason"),
            error=row.get("error"),
            created_at=created_at,
            finished_at=finished_at,
        )


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


runs_repository = RunsRepository()
