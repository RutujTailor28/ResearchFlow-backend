"""Supabase repository for append-only agent_events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from postgrest.exceptions import APIError

from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.supabase import get_supabase_client
from app.schemas.agent_event import AgentEventResponse, AgentEventType, AgentRole

logger = get_logger(__name__)
TABLE = "agent_events"


class AgentEventsRepository:
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

    def append(self, row: dict[str, Any]) -> AgentEventResponse:
        try:
            result = self.client.table(TABLE).insert(row).execute()
        except APIError as e:
            raise self._map(e, "append") from e
        if not result.data:
            raise AppError("Failed to append agent event", status_code=500)
        return self._to(result.data[0])

    def get_by_id(self, run_id: UUID, event_id: UUID) -> AgentEventResponse:
        try:
            result = (
                self.client.table(TABLE)
                .select("*")
                .eq("run_id", str(run_id))
                .eq("id", str(event_id))
                .limit(1)
                .execute()
            )
        except APIError as e:
            raise self._map(e, "get") from e
        if not result.data:
            raise NotFoundError(
                "Agent event not found",
                detail={"run_id": str(run_id), "event_id": str(event_id)},
            )
        return self._to(result.data[0])

    def list_for_run(
        self,
        run_id: UUID,
        *,
        event_type: Optional[AgentEventType] = None,
        agent_name: Optional[str] = None,
        after_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AgentEventResponse], int]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        # after_id requires chronological cursor filtering; load then slice in Python
        # so ordering stays consistent across PostgREST/Supabase.
        if after_id is not None:
            all_items = self._fetch_filtered(
                run_id, event_type=event_type, agent_name=agent_name
            )
            anchor = next((e for e in all_items if e.id == after_id), None)
            if anchor is None:
                # Validate ownership / existence for this run.
                self.get_by_id(run_id, after_id)
                raise NotFoundError(
                    "after_id not found in filtered event history",
                    detail={"after_id": str(after_id)},
                )
            idx = next(i for i, e in enumerate(all_items) if e.id == after_id)
            sliced = all_items[idx + 1 :]
            total = len(sliced)
            page = sliced[offset : offset + limit]
            return page, total

        try:
            query = (
                self.client.table(TABLE)
                .select("*", count="exact")
                .eq("run_id", str(run_id))
            )
            if event_type is not None:
                query = query.eq("event_type", event_type.value)
            if agent_name:
                query = query.eq("agent_name", agent_name)

            result = (
                query.order("created_at", desc=False)
                .order("id", desc=False)
                .range(offset, offset + limit - 1)
                .execute()
            )
        except APIError as e:
            raise self._map(e, "list") from e

        items = [self._to(r) for r in (result.data or [])]
        total = int(result.count) if result.count is not None else len(items)
        return items, total

    def _fetch_filtered(
        self,
        run_id: UUID,
        *,
        event_type: Optional[AgentEventType] = None,
        agent_name: Optional[str] = None,
    ) -> list[AgentEventResponse]:
        try:
            query = self.client.table(TABLE).select("*").eq("run_id", str(run_id))
            if event_type is not None:
                query = query.eq("event_type", event_type.value)
            if agent_name:
                query = query.eq("agent_name", agent_name)
            result = (
                query.order("created_at", desc=False).order("id", desc=False).execute()
            )
        except APIError as e:
            raise self._map(e, "fetch_filtered") from e
        return [self._to(r) for r in (result.data or [])]

    def list_all_chronological(self, run_id: UUID) -> list[AgentEventResponse]:
        items: list[AgentEventResponse] = []
        offset = 0
        page = 100
        while True:
            batch, total = self.list_for_run(run_id, limit=page, offset=offset)
            items.extend(batch)
            offset += len(batch)
            if not batch or offset >= total:
                break
        return items

    def count_for_run(self, run_id: UUID) -> int:
        try:
            result = (
                self.client.table(TABLE)
                .select("id", count="exact")
                .eq("run_id", str(run_id))
                .limit(1)
                .execute()
            )
        except APIError as e:
            raise self._map(e, "count") from e
        return int(result.count or 0)

    @staticmethod
    def _to(row: dict[str, Any]) -> AgentEventResponse:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        payload = row.get("payload_json") or {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return AgentEventResponse(
            id=UUID(str(row["id"])),
            run_id=UUID(str(row["run_id"])),
            agent_name=row["agent_name"],
            agent_role=AgentRole(row["agent_role"]),
            event_type=AgentEventType(row["event_type"]),
            payload_json=payload,
            tokens_in=int(row.get("tokens_in") or 0),
            tokens_out=int(row.get("tokens_out") or 0),
            cost_usd=float(row.get("cost_usd") or 0),
            created_at=created_at,
        )

    @staticmethod
    def _map(exc: APIError, action: str) -> AppError:
        message = str(getattr(exc, "message", None) or exc)
        code = getattr(exc, "code", None)
        logger.error(
            "Supabase agent_events.%s failed code=%s message=%s",
            action,
            code,
            message,
        )
        if code == "PGRST205" or "Could not find the table" in message:
            return AppError(
                "agent_events table is missing. Apply migrations/006_create_agent_events.sql",
                status_code=503,
                detail={"code": code, "action": action},
            )
        return AppError("Database operation failed", status_code=502, detail={"code": code})


agent_events_repository = AgentEventsRepository()
