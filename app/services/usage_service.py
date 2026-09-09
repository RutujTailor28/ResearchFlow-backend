"""Usage / cost / history aggregation from agent_events (Phase 9)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.core.exceptions import ForbiddenError
from app.core.logging import get_logger
from app.repositories.agent_events_repository import (
    AgentEventsRepository,
    agent_events_repository,
)
from app.repositories.findings_repository import FindingsRepository, findings_repository
from app.repositories.runs_repository import RunsRepository, runs_repository
from app.repositories.sources_repository import SourcesRepository, sources_repository
from app.schemas.agent_event import AgentEventResponse, AgentEventType
from app.schemas.usage import (
    AgentUsageItem,
    RunCostResponse,
    RunHistoryDetailResponse,
    RunHistoryItem,
    RunUsageResponse,
    TimelineEvent,
)
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)

# Events that carry leaf OpenRouter usage (avoid double-counting *_finished rollups).
_LEAF_USAGE_TYPES = {
    AgentEventType.tool_result,
    AgentEventType.section_completed,
}


class UsageService:
    def __init__(
        self,
        runs: RunService | None = None,
        runs_repo: RunsRepository | None = None,
        events: AgentEventsRepository | None = None,
        sources: SourcesRepository | None = None,
        findings: FindingsRepository | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._runs_repo = runs_repo or runs_repository
        self._events = events or agent_events_repository
        self._sources = sources or sources_repository
        self._findings = findings or findings_repository

    def get_usage(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> RunUsageResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        events = self._events.list_all_chronological(run.id)
        agents = self._aggregate_agents(events)
        total_in = sum(a.tokens_in for a in agents)
        total_out = sum(a.tokens_out for a in agents)
        total_cost = round(sum(a.cost_usd for a in agents), 6)
        models = sorted({a.model for a in agents if a.model})
        # Best-effort snapshot onto run row (never invent cost).
        try:
            fields: dict = {"cost_usd": total_cost}
            if run.finished_at and run.created_at:
                fields["duration_ms"] = int(
                    (run.finished_at - run.created_at).total_seconds() * 1000
                )
            elif run.duration_ms is None and events:
                start = events[0].created_at
                end = events[-1].created_at
                fields["duration_ms"] = max(
                    0, int((end - start).total_seconds() * 1000)
                )
            self._runs_repo.update(run.id, fields)
        except Exception:  # noqa: BLE001
            logger.debug("Could not snapshot cost/duration onto run %s", run.id)

        return RunUsageResponse(
            run_id=run.id,
            agents=agents,
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            total_tokens=total_in + total_out,
            total_cost_usd=total_cost,
            cost_by_agent={a.agent_name: a.cost_usd for a in agents if a.cost_usd},
            models_used=models,
        )

    def get_cost(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> RunCostResponse:
        usage = self.get_usage(run_id, user_id=user_id)
        by_role: dict[str, float] = {}
        for agent in usage.agents:
            role = agent.agent_role.value if agent.agent_role else "unknown"
            by_role[role] = round(by_role.get(role, 0.0) + agent.cost_usd, 6)
        return RunCostResponse(
            run_id=usage.run_id,
            total_cost_usd=usage.total_cost_usd,
            cost_by_agent=usage.cost_by_agent,
            cost_by_role=by_role,
            agents=usage.agents,
        )

    def get_history(
        self, run_id: UUID, *, user_id: Optional[UUID] = None
    ) -> RunHistoryDetailResponse:
        self._authorized_run(run_id, user_id=user_id)
        usage = self.get_usage(run_id, user_id=user_id)
        # Re-read after usage snapshot may update cost_usd / duration_ms.
        run = self._runs.get_run(run_id)
        source_count = len(self._sources.list_for_run(run.id))
        finding_count = len(self._findings.list_for_run(run.id))
        duration_ms = run.duration_ms
        if duration_ms is None and run.finished_at and run.created_at:
            duration_ms = int(
                (run.finished_at - run.created_at).total_seconds() * 1000
            )

        item = RunHistoryItem(
            id=run.id,
            topic=run.topic,
            status=run.status,
            duration_ms=duration_ms,
            total_cost_usd=usage.total_cost_usd,
            source_count=source_count,
            finding_count=finding_count,
            created_at=run.created_at,
            finished_at=run.finished_at,
        )
        events = self._events.list_all_chronological(run.id)
        timeline = [
            TimelineEvent(
                id=e.id,
                timestamp=e.created_at,
                agent_name=e.agent_name,
                agent_role=e.agent_role,
                event_type=e.event_type.value,
                payload_json=e.payload_json or {},
                tokens_in=e.tokens_in,
                tokens_out=e.tokens_out,
                cost_usd=e.cost_usd,
            )
            for e in events
        ]
        return RunHistoryDetailResponse(
            run=item,
            timeline=timeline,
            timeline_count=len(timeline),
        )

    def _aggregate_agents(
        self, events: list[AgentEventResponse]
    ) -> list[AgentUsageItem]:
        by_name: dict[str, list[AgentEventResponse]] = {}
        for event in events:
            by_name.setdefault(event.agent_name, []).append(event)

        items: list[AgentUsageItem] = []
        for name, evts in sorted(by_name.items(), key=lambda x: x[0]):
            leaf = [e for e in evts if e.event_type in _LEAF_USAGE_TYPES]
            billable = leaf if leaf else [
                e for e in evts if (e.tokens_in or e.tokens_out or e.cost_usd)
            ]
            tokens_in = sum(e.tokens_in for e in billable)
            tokens_out = sum(e.tokens_out for e in billable)
            # Never invent cost — sum only recorded cost_usd values.
            cost = round(sum(float(e.cost_usd or 0) for e in billable), 6)
            model = None
            for e in reversed(evts):
                payload = e.payload_json or {}
                if payload.get("model"):
                    model = str(payload["model"])
                    break
            role = evts[0].agent_role if evts else None
            items.append(
                AgentUsageItem(
                    agent_name=name,
                    agent_role=role,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    tokens_total=tokens_in + tokens_out,
                    cost_usd=cost,
                    model=model,
                    event_count=len(evts),
                )
            )
        return items

    def _authorized_run(self, run_id: UUID, *, user_id: Optional[UUID]):
        run = self._runs.get_run(run_id)
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run


usage_service = UsageService()
