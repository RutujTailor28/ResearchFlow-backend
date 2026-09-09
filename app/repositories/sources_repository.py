"""Supabase repository for research sources."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from postgrest.exceptions import APIError

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.supabase import get_supabase_client
from app.schemas.source import SourceResponse

logger = get_logger(__name__)

TABLE = "sources"


class SourcesRepository:
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

    def list_for_run(self, run_id: UUID) -> list[SourceResponse]:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .order("quality_score", desc=True)
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="list") from exc
        return [self._to_response(row) for row in (result.data or [])]

    def get_for_run(self, run_id: UUID, source_id: UUID) -> SourceResponse:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .eq("id", str(source_id))
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="get") from exc
        if not result.data:
            raise NotFoundError(
                "Source not found for this run",
                detail={"run_id": str(run_id), "source_id": str(source_id)},
            )
        return self._to_response(result.data[0])

    def find_by_url(self, run_id: UUID, url: str) -> Optional[SourceResponse]:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .eq("url", url)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="find_url") from exc
        if not result.data:
            return None
        return self._to_response(result.data[0])

    def find_by_content_hash(
        self, run_id: UUID, content_hash: str
    ) -> Optional[SourceResponse]:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .eq("content_hash", content_hash)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._map_api_error(exc, action="find_hash") from exc
        if not result.data:
            return None
        return self._to_response(result.data[0])

    def create(self, row: dict[str, Any]) -> SourceResponse:
        try:
            result = self.client.table(TABLE).insert(row).execute()
        except APIError as exc:
            # Unique violations → conflict
            message = str(getattr(exc, "message", exc))
            code = getattr(exc, "code", None)
            if code in {"23505", "PGRST116"} or "duplicate" in message.lower():
                raise ConflictError(
                    "Duplicate source for this run",
                    detail={"code": code},
                ) from exc
            raise self._map_api_error(exc, action="create") from exc
        if not result.data:
            raise AppError("Failed to create source", status_code=500)
        return self._to_response(result.data[0])

    def delete(self, run_id: UUID, source_id: UUID) -> None:
        self.get_for_run(run_id, source_id)
        try:
            self.client.table(TABLE).delete().eq("run_id", str(run_id)).eq(
                "id", str(source_id)
            ).execute()
        except APIError as exc:
            raise self._map_api_error(exc, action="delete") from exc

    @staticmethod
    def _to_response(row: dict[str, Any]) -> SourceResponse:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        published_at = row.get("published_at")
        if isinstance(published_at, str):
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))

        return SourceResponse(
            id=UUID(str(row["id"])),
            run_id=UUID(str(row["run_id"])),
            url=row["url"],
            domain=row["domain"],
            title=row.get("title") or "",
            published_at=published_at,
            quality_score=float(row.get("quality_score") or 0),
            content_hash=row["content_hash"],
            extract_text=row.get("extract_text") or "",
            created_at=created_at,
        )

    @staticmethod
    def _map_api_error(exc: APIError, *, action: str) -> AppError:
        message = str(getattr(exc, "message", None) or exc)
        code = getattr(exc, "code", None)
        logger.error(
            "Supabase sources.%s failed code=%s message=%s",
            action,
            code,
            message,
        )
        if code == "PGRST205" or "Could not find the table" in message:
            return AppError(
                "sources table is missing. Apply migrations/004_create_sources.sql",
                status_code=503,
                detail={"code": code, "action": action},
            )
        return AppError(
            "Database operation failed",
            status_code=502,
            detail={"code": code, "action": action},
        )


sources_repository = SourcesRepository()
