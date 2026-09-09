"""Multi-agent research orchestration (Phase 5)."""

from __future__ import annotations

import threading
from typing import Optional
from uuid import UUID, uuid4

from app.agents.research_graph import run_research_graph
from app.agents.researcher_agent import ResearcherAgent, ResearcherOutcome
from app.core.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError, ValidationAppError
from app.core.logging import get_logger
from app.core.run_control import RunAborted, StopReason, run_control
from app.repositories.findings_repository import FindingsRepository, findings_repository
from app.repositories.plan_sections_repository import (
    PlanSectionsRepository,
    plan_sections_repository,
)
from app.repositories.research_tasks_repository import (
    ResearchTasksRepository,
    research_tasks_repository,
)
from app.repositories.sources_repository import SourcesRepository, sources_repository
from app.schemas.agent_event import AgentEventType, AgentRole
from app.schemas.plan import SectionStatus
from app.schemas.research_run import (
    ResearchResultsResponse,
    ResearchStartResponse,
    ResearchStatusResponse,
    ResearchTaskStatus,
)
from app.schemas.run import RunStatus
from app.services.agent_event_service import (
    AgentEventService,
    agent_event_service,
    researcher_agent_name,
)
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)


class ResearchOrchestrationService:
    """
    Starts LangGraph fan-out research for an approved plan.
    Individual researcher failures do not abort the run.
    """

    def __init__(
        self,
        runs: RunService | None = None,
        sections: PlanSectionsRepository | None = None,
        tasks: ResearchTasksRepository | None = None,
        findings: FindingsRepository | None = None,
        sources: SourcesRepository | None = None,
        researcher: ResearcherAgent | None = None,
        events: AgentEventService | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._sections = sections or plan_sections_repository
        self._tasks = tasks or research_tasks_repository
        self._findings = findings or findings_repository
        self._sources = sources or sources_repository
        self._events = events or agent_event_service
        self._researcher = researcher or ResearcherAgent(events=self._events)
        self._lock = threading.Lock()
        self._active: set[UUID] = set()

    def _emit(
        self,
        run_id: UUID,
        event_type: AgentEventType,
        payload: dict | None = None,
        *,
        agent_name: str = "orchestrator",
        agent_role: AgentRole = AgentRole.orchestrator,
    ) -> None:
        try:
            self._events.emit(
                run_id=run_id,
                agent_name=agent_name,
                agent_role=agent_role,
                event_type=event_type,
                payload=payload or {},
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to emit orchestration event type=%s", event_type.value)

    def start_research(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
        sync: bool = False,
    ) -> ResearchStartResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        if run.status != RunStatus.approved:
            raise ConflictError(
                f"Research can only start when run status is 'approved' "
                f"(current: '{run.status.value}')",
                detail={"run_id": str(run_id), "status": run.status.value},
            )

        sections = self._sections.list_records_for_run(run.id)
        if not sections:
            raise ValidationAppError(
                "Cannot start research without plan sections",
                detail={"run_id": str(run_id)},
            )

        task_rows: list[dict] = []
        agent_index = 0
        for section in sections:
            for question in section.sub_questions:
                q = (question or "").strip()
                if len(q) < 3:
                    continue
                task_rows.append(
                    {
                        "id": str(uuid4()),
                        "run_id": str(run.id),
                        "section_id": str(section.id),
                        "sub_question": q,
                        "status": ResearchTaskStatus.pending.value,
                        "tool_calls": 0,
                        "agent_name": researcher_agent_name(agent_index),
                    }
                )
                agent_index += 1

        if not task_rows:
            raise ValidationAppError(
                "Plan has no valid sub-questions to research",
                detail={"run_id": str(run_id)},
            )

        with self._lock:
            if run.id in self._active:
                raise ConflictError(
                    "Research is already running for this run",
                    detail={"run_id": str(run_id)},
                )
            # Replace any leftover tasks from a prior aborted attempt.
            self._tasks.delete_for_run(run.id)
            db_rows = [
                {k: v for k, v in row.items() if k != "agent_name"} for row in task_rows
            ]
            created = self._tasks.create_many(db_rows)
            self._runs.update_run_status(
                run.id,
                RunStatus.running,
                clear_cancel=True,
                set_started=True,
            )
            for section in sections:
                try:
                    self._sections.update_fields(
                        run_id=run.id,
                        section_id=section.id,
                        fields={"status": SectionStatus.researching.value},
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Could not mark section researching id=%s", section.id
                    )
            self._active.add(run.id)

        self._emit(
            run.id,
            AgentEventType.agent_started,
            {
                "message": "Multi-agent research started",
                "task_count": len(created),
                "topic": run.topic,
            },
        )

        logger.info(
            "Starting research run_id=%s tasks=%s sync=%s",
            run.id,
            len(created),
            sync,
        )

        if sync:
            self._execute(run.id, topic=run.topic, task_dicts=task_rows, retry=False)
        else:
            thread = threading.Thread(
                target=self._execute,
                kwargs={
                    "run_id": run.id,
                    "topic": run.topic,
                    "task_dicts": task_rows,
                    "retry": False,
                },
                name=f"research-{run.id}",
                daemon=True,
            )
            thread.start()

        return ResearchStartResponse(
            run_id=run.id,
            status=RunStatus.running.value,
            task_count=len(created),
            message="Research started",
        )

    def retry_research(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
        sync: bool = False,
    ) -> ResearchStartResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        if run.status not in {
            RunStatus.partial,
            RunStatus.failed,
            RunStatus.completed,
            RunStatus.approved,
            RunStatus.running,
        }:
            raise ConflictError(
                f"Cannot retry research when run status is '{run.status.value}'",
                detail={"run_id": str(run_id), "status": run.status.value},
            )

        failed = self._tasks.list_failed(run.id)
        if not failed:
            raise ValidationAppError(
                "No failed research tasks to retry",
                detail={"run_id": str(run_id)},
            )

        with self._lock:
            if run.id in self._active:
                raise ConflictError(
                    "Research is already running for this run",
                    detail={"run_id": str(run_id)},
                )
            task_dicts: list[dict] = []
            for index, task in enumerate(failed):
                self._tasks.update(
                    task.id,
                    {
                        "status": ResearchTaskStatus.pending.value,
                        "tool_calls": 0,
                        "error": None,
                        "gap_note": None,
                        "finished_at": None,
                    },
                )
                task_dicts.append(
                    {
                        "id": str(task.id),
                        "run_id": str(run.id),
                        "section_id": str(task.section_id),
                        "sub_question": task.sub_question,
                        "agent_name": researcher_agent_name(index),
                    }
                )
            self._runs.update_run_status(
                run.id,
                RunStatus.running,
                clear_cancel=True,
                set_started=True,
            )
            self._active.add(run.id)

        self._emit(
            run.id,
            AgentEventType.retry,
            {
                "message": "Retrying failed researchers",
                "task_count": len(task_dicts),
            },
        )
        self._emit(
            run.id,
            AgentEventType.agent_started,
            {
                "message": "Research retry started",
                "task_count": len(task_dicts),
            },
        )

        logger.info("Retrying research run_id=%s failed_tasks=%s", run.id, len(failed))

        if sync:
            self._execute(run.id, topic=run.topic, task_dicts=task_dicts, retry=True)
        else:
            thread = threading.Thread(
                target=self._execute,
                kwargs={
                    "run_id": run.id,
                    "topic": run.topic,
                    "task_dicts": task_dicts,
                    "retry": True,
                },
                name=f"research-retry-{run.id}",
                daemon=True,
            )
            thread.start()

        return ResearchStartResponse(
            run_id=run.id,
            status=RunStatus.running.value,
            task_count=len(task_dicts),
            message="Research retry started",
        )

    def get_status(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> ResearchStatusResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        tasks = self._tasks.list_for_run(run.id)
        findings = self._findings.list_for_run(run.id)
        counts = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        for task in tasks:
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        return ResearchStatusResponse(
            run_id=run.id,
            run_status=run.status.value,
            total_tasks=len(tasks),
            pending=counts["pending"],
            running=counts["running"],
            completed=counts["completed"],
            failed=counts["failed"],
            findings_count=len(findings),
            tasks=tasks,
        )

    def get_results(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> ResearchResultsResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        tasks = self._tasks.list_for_run(run.id)
        findings = self._findings.list_for_run(run.id)
        gaps = [f for f in findings if f.is_gap]
        return ResearchResultsResponse(
            run_id=run.id,
            run_status=run.status.value,
            findings_count=len(findings),
            gaps_count=len(gaps),
            tasks=tasks,
            findings=[f.model_dump(mode="json") for f in findings],
        )

    def _execute(
        self,
        run_id: UUID,
        *,
        topic: str,
        task_dicts: list[dict],
        retry: bool,
    ) -> None:
        settings = get_settings()
        abort_reason: StopReason = StopReason.none
        try:
            run = self._runs.get_run(run_id)
            run_control.begin(
                run_id,
                cost_limit_usd=run.cost_limit_usd,
                timeout_s=settings.run_timeout_s,
            )
            # Seed with already-spent cost so ceiling covers the whole run.
            if run.cost_usd:
                run_control.add_cost(run_id, float(run.cost_usd))

            # Ensure researcher has the event emitter (tests may swap agent).
            if self._researcher is not None and getattr(self._researcher, "_events", None) is None:
                self._researcher._events = self._events

            for task in task_dicts:
                if self._runs.is_cancel_requested(run_id):
                    abort_reason = StopReason.cancelled
                    break
                try:
                    self._tasks.mark_running(UUID(str(task["id"])))
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to mark task running id=%s", task.get("id"))

            if abort_reason == StopReason.none:
                self._emit(
                    run_id,
                    AgentEventType.thinking,
                    {
                        "message": "Fan-out to parallel researchers",
                        "task_count": len(task_dicts),
                        "retry": retry,
                    },
                )

                outcomes = run_research_graph(
                    run_id=run_id,
                    topic=topic,
                    tasks=task_dicts,
                    researcher=self._researcher,
                    max_workers=int(settings.research_max_workers),
                )
                self._persist_outcomes(run_id, outcomes)
                abort_reason = run_control.stop_reason(run_id)
                if abort_reason == StopReason.none:
                    abort_reason = run_control.check(
                        run_id,
                        db_cancel=self._runs.is_cancel_requested(run_id),
                    )

            self._finalize_run(run_id, abort_reason=abort_reason)
        except RunAborted as exc:
            logger.info(
                "Research aborted run_id=%s reason=%s", run_id, exc.reason.value
            )
            self._emit(
                run_id,
                AgentEventType.error,
                {"error": exc.reason.value, "aborted": True},
            )
            self._finalize_run(run_id, abort_reason=exc.reason)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Research orchestration crashed run_id=%s", run_id)
            self._emit(
                run_id,
                AgentEventType.error,
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            try:
                current = self._runs.get_run(run_id)
                if not (current.cancel_requested or current.status == RunStatus.cancelled):
                    self._runs.update_run_status(
                        run_id,
                        RunStatus.failed,
                        error=f"{type(exc).__name__}: {exc}",
                        stop_reason="error",
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to mark run failed after crash")
            self._emit(
                run_id,
                AgentEventType.agent_finished,
                {"success": False, "crashed": True},
            )
        finally:
            run_control.end(run_id)
            with self._lock:
                self._active.discard(run_id)

    def _persist_outcomes(
        self, run_id: UUID, outcomes: list[ResearcherOutcome]
    ) -> None:
        section_success: set[UUID] = set()
        section_fail: set[UUID] = set()

        for outcome in outcomes:
            if outcome.success and outcome.claim:
                source_id = self._ensure_source(run_id, outcome)
                self._findings.create(
                    {
                        "id": str(uuid4()),
                        "run_id": str(run_id),
                        "section_id": str(outcome.section_id),
                        "claim": outcome.claim,
                        "source_id": str(source_id) if source_id else None,
                        "confidence": outcome.confidence,
                        "is_gap": False,
                    }
                )
                self._tasks.mark_completed(
                    outcome.task_id, tool_calls=outcome.tool_calls
                )
                section_success.add(outcome.section_id)
            else:
                gap_claim = (
                    outcome.gap_note
                    or f"Research gap for: {outcome.sub_question}"
                )[:4000]
                self._findings.create(
                    {
                        "id": str(uuid4()),
                        "run_id": str(run_id),
                        "section_id": str(outcome.section_id),
                        "claim": gap_claim,
                        "source_id": None,
                        "confidence": 0.0,
                        "is_gap": True,
                    }
                )
                self._tasks.mark_failed(
                    outcome.task_id,
                    tool_calls=outcome.tool_calls,
                    error=outcome.error or "Research failed",
                    gap_note=outcome.gap_note,
                )
                section_fail.add(outcome.section_id)

        # Update section statuses based on outcomes in this batch.
        touched = section_success | section_fail
        for section_id in touched:
            status = (
                SectionStatus.researched
                if section_id in section_success and section_id not in section_fail
                else SectionStatus.failed
                if section_id in section_fail and section_id not in section_success
                else SectionStatus.researched
            )
            # Mixed success within a section still counts as researched with gaps.
            if section_id in section_success:
                status = SectionStatus.researched
            try:
                self._sections.update_fields(
                    run_id=run_id,
                    section_id=section_id,
                    fields={"status": status.value},
                )
            except Exception:  # noqa: BLE001
                logger.warning("Could not update section status id=%s", section_id)

    def _ensure_source(
        self, run_id: UUID, outcome: ResearcherOutcome
    ) -> Optional[UUID]:
        payload = outcome.source_payload
        if not payload:
            return None
        url = payload.get("url")
        content_hash = payload.get("content_hash")
        if not url or not content_hash:
            return None

        existing = self._sources.find_by_url(run_id, url)
        if existing is not None:
            return existing.id
        existing_hash = self._sources.find_by_content_hash(run_id, content_hash)
        if existing_hash is not None:
            return existing_hash.id

        try:
            created = self._sources.create(
                {
                    "id": payload.get("id") or str(uuid4()),
                    "run_id": str(run_id),
                    "url": url,
                    "domain": payload.get("domain") or "unknown",
                    "title": (payload.get("title") or "Untitled")[:500],
                    "published_at": None,
                    "quality_score": float(payload.get("quality_score") or 0),
                    "content_hash": content_hash,
                    "extract_text": payload.get("extract_text") or "",
                }
            )
            return created.id
        except Exception as exc:  # noqa: BLE001
            logger.warning("Source persist skipped: %s", type(exc).__name__)
            # Race: another worker may have inserted — try lookup again.
            existing = self._sources.find_by_url(run_id, url)
            return existing.id if existing else None

    def _finalize_run(
        self,
        run_id: UUID,
        *,
        abort_reason: StopReason = StopReason.none,
    ) -> None:
        current = self._runs.get_run(run_id)
        # Preserve cooperative cancel — do not overwrite cancelled status.
        if current.cancel_requested or current.status == RunStatus.cancelled:
            self._emit(
                run_id,
                AgentEventType.agent_finished,
                {
                    "success": False,
                    "status": RunStatus.cancelled.value,
                    "stop_reason": "cancelled",
                    "message": "Research stopped after cancellation; findings preserved",
                },
            )
            return

        tasks = self._tasks.list_for_run(run_id)
        if not tasks:
            self._runs.update_run_status(
                run_id, RunStatus.failed, error="No tasks", stop_reason="error"
            )
            self._emit(
                run_id,
                AgentEventType.agent_finished,
                {"success": False, "error": "No tasks"},
            )
            return

        failed = sum(1 for t in tasks if t.status == ResearchTaskStatus.failed)
        completed = sum(1 for t in tasks if t.status == ResearchTaskStatus.completed)
        pending = sum(
            1
            for t in tasks
            if t.status
            in {ResearchTaskStatus.pending, ResearchTaskStatus.running}
        )

        if abort_reason == StopReason.cost_limit:
            status = RunStatus.partial
            error = "Cost ceiling reached; partial results saved"
            stop = StopReason.cost_limit.value
        elif abort_reason == StopReason.timeout:
            status = RunStatus.partial
            error = "Global run timeout reached; partial results saved"
            stop = StopReason.timeout.value
        elif abort_reason == StopReason.cancelled:
            status = RunStatus.cancelled
            error = None
            stop = StopReason.cancelled.value
        elif failed == 0 and completed == len(tasks) and pending == 0:
            status = RunStatus.completed
            error = None
            stop = None
        elif completed == 0 and failed > 0:
            status = RunStatus.failed
            error = f"{failed} researcher(s) failed"
            stop = "error"
        else:
            status = RunStatus.partial
            error = f"{failed} of {len(tasks)} researcher(s) failed"
            stop = "partial"

        # Snapshot spend onto the run row (never invent).
        spent = run_control.current_cost(run_id)
        if spent > 0:
            try:
                self._runs.update_fields(run_id, {"cost_usd": spent})
            except Exception:  # noqa: BLE001
                pass

        self._runs.update_run_status(
            run_id,
            status,
            error=error,
            stop_reason=stop,
        )
        self._emit(
            run_id,
            AgentEventType.agent_finished,
            {
                "success": failed == 0 and abort_reason == StopReason.none,
                "status": status.value,
                "completed": completed,
                "failed": failed,
                "stop_reason": stop,
                "message": f"Research orchestration finished ({status.value})",
            },
        )

    def _authorized_run(self, run_id: UUID, *, user_id: Optional[UUID]):
        run = self._runs.get_run(run_id)
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run


research_orchestration_service = ResearchOrchestrationService()
