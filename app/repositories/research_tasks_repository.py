"""Supabase repository for research tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from postgrest.exceptions import APIError

from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.supabase import get_supabase_client
from app.schemas.research_run import ResearchTaskResponse, ResearchTaskStatus

logger = get_logger(__name__)
TABLE = "research_tasks"


class ResearchTasksRepository:
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

    def list_for_run(self, run_id: UUID) -> list[ResearchTaskResponse]:
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

    def list_failed(self, run_id: UUID) -> list[ResearchTaskResponse]:
        return [t for t in self.list_for_run(run_id) if t.status == ResearchTaskStatus.failed]

    def create_many(self, rows: list[dict[str, Any]]) -> list[ResearchTaskResponse]:
        if not rows:
            return []
        try:
            result = self.client.table(TABLE).insert(rows).execute()
        except APIError as e:
            raise self._map(e, "create_many") from e
        return [self._to(r) for r in (result.data or [])]

    def update(self, task_id: UUID, fields: dict[str, Any]) -> ResearchTaskResponse:
        try:
            result = self.client.table(TABLE).update(fields).eq("id", str(task_id)).execute()
        except APIError as e:
            raise self._map(e, "update") from e
        if not result.data:
            raise NotFoundError("Research task not found", detail={"task_id": str(task_id)})
        return self._to(result.data[0])

    def delete_for_run(self, run_id: UUID) -> None:
        try:
            self.client.table(TABLE).delete().eq("run_id", str(run_id)).execute()
        except APIError as e:
            raise self._map(e, "delete_for_run") from e

    def mark_running(self, task_id: UUID) -> ResearchTaskResponse:
        return self.update(task_id, {"status": ResearchTaskStatus.running.value})

    def mark_completed(self, task_id: UUID, *, tool_calls: int) -> ResearchTaskResponse:
        return self.update(
            task_id,
            {
                "status": ResearchTaskStatus.completed.value,
                "tool_calls": tool_calls,
                "error": None,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def mark_failed(
        self,
        task_id: UUID,
        *,
        tool_calls: int,
        error: str,
        gap_note: str | None = None,
    ) -> ResearchTaskResponse:
        return self.update(
            task_id,
            {
                "status": ResearchTaskStatus.failed.value,
                "tool_calls": tool_calls,
                "error": error[:2000],
                "gap_note": (gap_note or error)[:2000],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def _to(row: dict[str, Any]) -> ResearchTaskResponse:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        finished_at = row.get("finished_at")
        if isinstance(finished_at, str):
            finished_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        return ResearchTaskResponse(
            id=UUID(str(row["id"])),
            run_id=UUID(str(row["run_id"])),
            section_id=UUID(str(row["section_id"])),
            sub_question=row["sub_question"],
            status=ResearchTaskStatus(row["status"]),
            tool_calls=int(row.get("tool_calls") or 0),
            error=row.get("error"),
            gap_note=row.get("gap_note"),
            created_at=created_at,
            finished_at=finished_at,
        )

    @staticmethod
    def _map(exc: APIError, action: str) -> AppError:
        message = str(getattr(exc, "message", None) or exc)
        code = getattr(exc, "code", None)
        logger.error(
            "Supabase research_tasks.%s failed code=%s message=%s",
            action,
            code,
            message,
        )
        if code == "PGRST205" or "Could not find the table" in message:
            return AppError(
                "research_tasks table is missing. Apply migrations/005_create_findings_and_research_tasks.sql",
                status_code=503,
                detail={"code": code, "action": action},
            )
        return AppError("Database operation failed", status_code=502, detail={"code": code})


research_tasks_repository = ResearchTasksRepository()
