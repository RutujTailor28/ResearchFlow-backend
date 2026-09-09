"""Research run lifecycle service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.run_control import run_control
from app.core.run_lifecycle import assert_transition
from app.repositories.runs_repository import RunsRepository, runs_repository
from app.repositories.sources_repository import SourcesRepository, sources_repository
from app.schemas.run import (
    CreateRunRequest,
    RunListResponse,
    RunResponse,
    RunStatus,
    RunStatusResponse,
)

logger = get_logger(__name__)


class RunService:
    def __init__(
        self,
        repository: RunsRepository | None = None,
        sources: SourcesRepository | None = None,
    ) -> None:
        self._repo = repository or runs_repository
        self._sources = sources or sources_repository

    def create_run(self, payload: CreateRunRequest) -> RunResponse:
        settings = get_settings()
        run_id = uuid4()
        cost_limit = payload.cost_limit_usd
        if cost_limit is None:
            cost_limit = settings.run_cost_limit_usd
        row = {
            "id": str(run_id),
            "user_id": None,
            "topic": payload.topic,
            "depth": payload.depth.value,
            "audience": payload.audience.value,
            "tone": payload.tone.value,
            "target_length": payload.target_length,
            "source_types": list(payload.source_types),
            "status": RunStatus.plan_pending.value,
            "plan_json": None,
            "cost_usd": 0,
            "duration_ms": None,
            "error": None,
            "finished_at": None,
            "cancel_requested": False,
            "cost_limit_usd": cost_limit,
            "started_at": None,
            "stop_reason": None,
        }
        try:
            saved = self._repo.create(row)
        except Exception as exc:
            # Graceful degrade if migration 009 not applied yet.
            message = str(exc).lower()
            if "cancel_requested" in message or "cost_limit_usd" in message or "stop_reason" in message or "started_at" in message or "pgrst" in message:
                for key in (
                    "cancel_requested",
                    "cost_limit_usd",
                    "started_at",
                    "stop_reason",
                ):
                    row.pop(key, None)
                saved = self._repo.create(row)
            else:
                raise
        logger.info(
            "Created run id=%s topic=%r status=%s cost_limit=%s",
            saved.id,
            saved.topic,
            saved.status,
            cost_limit,
        )
        return saved

    def list_runs(
        self,
        *,
        status: Optional[RunStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> RunListResponse:
        """List runs with history fields (topic, status, duration, cost, source count, dates)."""
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        items, total = self._repo.list(status=status, limit=limit, offset=offset)
        enriched: list[RunResponse] = []
        for run in items:
            duration_ms = run.duration_ms
            if duration_ms is None and run.finished_at and run.created_at:
                duration_ms = int(
                    (run.finished_at - run.created_at).total_seconds() * 1000
                )
            try:
                source_count = len(self._sources.list_for_run(run.id))
            except Exception:  # noqa: BLE001 - list must not fail if sources unavailable
                source_count = None
            enriched.append(
                run.model_copy(
                    update={"duration_ms": duration_ms, "source_count": source_count}
                )
            )
        return RunListResponse(
            items=enriched, total=total, limit=limit, offset=offset
        )

    def get_run(self, run_id: UUID) -> RunResponse:
        run = self._repo.get_by_id(run_id)
        if run is None:
            raise NotFoundError("Run not found", detail={"run_id": str(run_id)})
        return run

    def get_status(self, run_id: UUID) -> RunStatusResponse:
        run = self.get_run(run_id)
        return RunStatusResponse(
            id=run.id,
            status=run.status,
            cancel_requested=run.cancel_requested,
            stop_reason=run.stop_reason,
            created_at=run.created_at,
            finished_at=run.finished_at,
            error=run.error,
        )

    def cancel_run(self, run_id: UUID) -> RunResponse:
        """
        Cooperative cancellation:
        - set cancel_requested immediately (in-memory + DB)
        - mark status cancelled
        - workers check the flag between steps and stop safely
        - completed findings already persisted are preserved
        """
        run = self.get_run(run_id)
        if run.status in {RunStatus.completed, RunStatus.cancelled}:
            raise ConflictError(
                f"Cannot cancel a run with status '{run.status.value}'",
                detail={"run_id": str(run_id), "status": run.status.value},
            )

        # In-memory flag first so workers stop quickly.
        run_control.request_cancel(run_id)

        if run.status == RunStatus.running:
            assert_transition(run.status, RunStatus.cancelled, run_id=str(run_id))
        elif run.status not in {
            RunStatus.plan_pending,
            RunStatus.plan_ready,
            RunStatus.approved,
            RunStatus.partial,
            RunStatus.failed,
            RunStatus.draft,
        }:
            assert_transition(run.status, RunStatus.cancelled, run_id=str(run_id))

        now = datetime.now(timezone.utc)
        saved = self._repo.update(
            run_id,
            {
                "cancel_requested": True,
                "status": RunStatus.cancelled.value,
                "finished_at": now.isoformat(),
                "stop_reason": "cancelled",
                "error": None,
            },
        )
        logger.info("Cancelled run id=%s (cooperative)", saved.id)
        return saved

    def delete_run(self, run_id: UUID) -> None:
        self._repo.delete(run_id)
        run_control.end(run_id)
        logger.info("Deleted run id=%s", run_id)

    def update_run_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        plan_json: dict[str, Any] | None = None,
        error: str | None = None,
        finished: bool = False,
        stop_reason: str | None = None,
        clear_cancel: bool = False,
        set_started: bool = False,
        skip_transition_check: bool = False,
    ) -> RunResponse:
        current = self.get_run(run_id)

        # Never overwrite a cooperative cancel with a later finalize status.
        if current.cancel_requested or current.status == RunStatus.cancelled:
            if status not in {RunStatus.cancelled}:
                logger.info(
                    "Ignoring status update run_id=%s %s→%s (cancel_requested)",
                    run_id,
                    current.status.value,
                    status.value,
                )
                return current

        if not skip_transition_check:
            assert_transition(current.status, status, run_id=str(run_id))

        fields: dict[str, Any] = {"status": status.value}
        if plan_json is not None:
            fields["plan_json"] = plan_json
        if error is not None:
            fields["error"] = error
        if stop_reason is not None:
            fields["stop_reason"] = stop_reason
        if finished or status in {
            RunStatus.completed,
            RunStatus.cancelled,
            RunStatus.failed,
            RunStatus.partial,
        }:
            fields["finished_at"] = datetime.now(timezone.utc).isoformat()
        if status == RunStatus.running:
            fields["finished_at"] = None
            fields["error"] = None
            fields["stop_reason"] = None
            if clear_cancel:
                fields["cancel_requested"] = False
            if set_started:
                fields["started_at"] = datetime.now(timezone.utc).isoformat()
        return self._repo.update(run_id, fields)

    def is_cancel_requested(self, run_id: UUID) -> bool:
        if run_control.is_cancel_requested(run_id):
            return True
        run = self.get_run(run_id)
        return bool(run.cancel_requested or run.status == RunStatus.cancelled)

    def update_fields(self, run_id: UUID, fields: dict[str, Any]) -> RunResponse:
        return self._repo.update(run_id, fields)


run_service = RunService()
