"""Planner service — AI plan generation and persistence (Phase 2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.agents.planner_agent import PlannerAgent, planner_agent
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.repositories.plan_sections_repository import (
    PlanSectionsRepository,
    plan_sections_repository,
)
from app.schemas.agent_event import AgentEventType, AgentRole
from app.schemas.plan import (
    ImprovePlanRequest,
    PlanDetailResponse,
    PlanMeta,
    PlanMutationResponse,
    PlanSection,
    ResearchPlan,
    UpdatePlanRequest,
)
from app.schemas.run import RunStatus
from app.services.agent_event_service import AgentEventService, agent_event_service
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)

APPROVABLE_STATUSES = {RunStatus.plan_ready}
GENERATABLE_BLOCKED = {RunStatus.running, RunStatus.completed, RunStatus.cancelled}
PLANNER_NAME = "planner-01"


def _usage_metrics(usage: dict[str, Any] | None) -> tuple[int, int, float]:
    if not usage:
        return 0, 0, 0.0
    tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cost = float(usage.get("cost") or usage.get("total_cost") or 0.0)
    return tokens_in, tokens_out, cost


class PlannerService:
    def __init__(
        self,
        runs: RunService | None = None,
        sections: PlanSectionsRepository | None = None,
        agent: PlannerAgent | None = None,
        events: AgentEventService | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._sections = sections or plan_sections_repository
        self._agent = agent or planner_agent
        self._events = events or agent_event_service

    def _emit(
        self,
        run_id: UUID,
        event_type: AgentEventType,
        payload: dict[str, Any] | None = None,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        try:
            self._events.emit(
                run_id=run_id,
                agent_name=PLANNER_NAME,
                agent_role=AgentRole.planner,
                event_type=event_type,
                payload=payload or {},
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to emit planner event type=%s", event_type.value)

    def generate_plan(self, run_id: UUID) -> PlanMutationResponse:
        run = self._runs.get_run(run_id)
        if run.status in GENERATABLE_BLOCKED:
            raise ConflictError(
                f"Cannot generate plan for run with status '{run.status.value}'",
                detail={"run_id": str(run_id), "status": run.status.value},
            )

        self._emit(
            run_id,
            AgentEventType.agent_started,
            {"message": "Generating research plan", "topic": run.topic},
        )
        self._emit(
            run_id,
            AgentEventType.thinking,
            {"message": "Decomposing topic into sections and sub-questions"},
        )
        self._emit(
            run_id,
            AgentEventType.tool_call,
            {"tool": "openrouter.chat", "operation": "plan"},
        )
        try:
            plan, ai_result = self._agent.generate_plan(run)
        except Exception as exc:
            self._emit(
                run_id,
                AgentEventType.error,
                {"error": f"{type(exc).__name__}: {exc}", "operation": "plan"},
            )
            self._emit(
                run_id,
                AgentEventType.agent_finished,
                {"success": False, "operation": "plan"},
            )
            raise

        tokens_in, tokens_out, cost = _usage_metrics(ai_result.usage)
        self._emit(
            run_id,
            AgentEventType.tool_result,
            {
                "tool": "openrouter.chat",
                "operation": "plan",
                "model": ai_result.model,
                "sections": len(plan.sections),
                "duration_ms": ai_result.duration_ms,
            },
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )
        saved = self._persist_plan(run_id, plan, status=RunStatus.plan_ready)
        self._emit(
            run_id,
            AgentEventType.agent_finished,
            {
                "success": True,
                "operation": "plan",
                "sections": len(saved.sections),
                "message": "Research plan formulated",
            },
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )
        logger.info(
            "Generated AI plan run_id=%s sections=%s model=%s",
            run_id,
            len(saved.sections),
            ai_result.model,
        )
        return PlanMutationResponse(
            plan=saved,
            run_status=RunStatus.plan_ready,
            meta=PlanMeta(
                generation_mode="ai",
                model=ai_result.model,
                requested_model=ai_result.requested_model,
                duration_ms=ai_result.duration_ms,
            ),
        )

    def get_plan(self, run_id: UUID) -> PlanDetailResponse:
        run = self._runs.get_run(run_id)
        sections = self._sections.list_for_run(run_id)
        title = run.topic
        summary = ""

        if run.plan_json and isinstance(run.plan_json, dict):
            title = str(run.plan_json.get("title") or title)
            summary = str(run.plan_json.get("summary") or "")
            if not sections and isinstance(run.plan_json.get("sections"), list):
                sections = [
                    PlanSection.model_validate(item)
                    for item in run.plan_json["sections"]
                ]

        if not sections and not (run.plan_json and run.plan_json.get("sections")):
            raise NotFoundError(
                "Plan not found for this run. Generate a plan first.",
                detail={"run_id": str(run_id)},
            )

        return PlanDetailResponse(
            run={
                "id": str(run.id),
                "topic": run.topic,
                "depth": run.depth.value,
                "audience": run.audience,
                "tone": run.tone,
                "target_length": run.target_length,
                "source_types": run.source_types,
                "status": run.status.value,
            },
            title=title,
            summary=summary,
            status=run.status,
            sections=sections,
        )

    def update_plan(self, run_id: UUID, payload: UpdatePlanRequest) -> PlanMutationResponse:
        run = self._runs.get_run(run_id)
        if run.status in {RunStatus.running, RunStatus.completed, RunStatus.cancelled}:
            raise ConflictError(
                f"Cannot update plan for run with status '{run.status.value}'",
                detail={"run_id": str(run_id), "status": run.status.value},
            )

        plan = ResearchPlan(
            title=payload.title,
            summary=payload.summary,
            sections=payload.sections,
        )
        saved = self._persist_plan(run_id, plan, status=RunStatus.plan_ready)
        logger.info("Updated plan run_id=%s sections=%s", run_id, len(saved.sections))
        return PlanMutationResponse(plan=saved, run_status=RunStatus.plan_ready)

    def improve_plan(
        self, run_id: UUID, payload: ImprovePlanRequest
    ) -> PlanMutationResponse:
        run = self._runs.get_run(run_id)
        current = self._load_current_plan(run_id, run.plan_json)
        self._emit(
            run_id,
            AgentEventType.agent_started,
            {"message": "Improving research plan", "instructions": payload.instructions},
        )
        self._emit(
            run_id,
            AgentEventType.tool_call,
            {"tool": "openrouter.chat", "operation": "improve-plan"},
        )
        try:
            improved, ai_result = self._agent.improve_plan(
                run, current, payload.instructions.strip()
            )
        except Exception as exc:
            self._emit(
                run_id,
                AgentEventType.error,
                {"error": f"{type(exc).__name__}: {exc}", "operation": "improve-plan"},
            )
            self._emit(
                run_id,
                AgentEventType.agent_finished,
                {"success": False, "operation": "improve-plan"},
            )
            raise

        tokens_in, tokens_out, cost = _usage_metrics(ai_result.usage)
        self._emit(
            run_id,
            AgentEventType.tool_result,
            {
                "tool": "openrouter.chat",
                "operation": "improve-plan",
                "model": ai_result.model,
                "sections": len(improved.sections),
                "duration_ms": ai_result.duration_ms,
            },
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )
        saved = self._persist_plan(run_id, improved, status=RunStatus.plan_ready)
        self._emit(
            run_id,
            AgentEventType.agent_finished,
            {
                "success": True,
                "operation": "improve-plan",
                "sections": len(saved.sections),
            },
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )
        logger.info(
            "Improved AI plan run_id=%s sections=%s model=%s",
            run_id,
            len(saved.sections),
            ai_result.model,
        )
        return PlanMutationResponse(
            plan=saved,
            run_status=RunStatus.plan_ready,
            meta=PlanMeta(
                generation_mode="ai",
                model=ai_result.model,
                requested_model=ai_result.requested_model,
                duration_ms=ai_result.duration_ms,
            ),
        )

    def approve_plan(self, run_id: UUID) -> PlanDetailResponse:
        run = self._runs.get_run(run_id)
        detail = self.get_plan(run_id)

        if run.status not in APPROVABLE_STATUSES:
            raise ConflictError(
                f"Cannot approve plan when run status is '{run.status.value}'",
                detail={
                    "run_id": str(run_id),
                    "status": run.status.value,
                    "allowed": [s.value for s in APPROVABLE_STATUSES],
                },
            )

        self._runs.update_run_status(run_id, RunStatus.approved)
        detail.status = RunStatus.approved
        detail.run["status"] = RunStatus.approved.value
        logger.info("Approved plan run_id=%s", run_id)
        return detail

    def _persist_plan(
        self,
        run_id: UUID,
        plan: ResearchPlan,
        *,
        status: RunStatus,
    ) -> ResearchPlan:
        sections = self._sections.replace_for_run(run_id, plan)
        plan_json: dict[str, Any] = {
            "title": plan.title,
            "summary": plan.summary,
            "sections": [s.model_dump(mode="json") for s in sections],
        }
        self._runs.update_run_status(run_id, status, plan_json=plan_json)
        return ResearchPlan(title=plan.title, summary=plan.summary, sections=sections)

    def _load_current_plan(
        self, run_id: UUID, plan_json: dict[str, Any] | None
    ) -> ResearchPlan:
        sections = self._sections.list_for_run(run_id)
        if sections and plan_json:
            return ResearchPlan(
                title=str(plan_json.get("title") or "Research Plan"),
                summary=str(plan_json.get("summary") or ""),
                sections=sections,
            )
        if plan_json and plan_json.get("sections"):
            return ResearchPlan.model_validate(plan_json)
        raise NotFoundError(
            "Plan not found for this run. Generate a plan first.",
            detail={"run_id": str(run_id)},
        )


planner_service = PlannerService()
