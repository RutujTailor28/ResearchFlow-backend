"""Reusable agent event emitter — persist + live fan-out (Phase 6).

Same event shape powers:
1. Live SSE streaming
2. Database persistence
3. Historical replay
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Callable, Iterator, Optional
from uuid import UUID, uuid4

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.repositories.agent_events_repository import (
    AgentEventsRepository,
    agent_events_repository,
)
from app.schemas.agent_event import (
    AgentEventListResponse,
    AgentEventResponse,
    AgentEventType,
    AgentListResponse,
    AgentRole,
    AgentRuntimeStatus,
    AgentsStatusSummaryResponse,
    AgentStatusResponse,
)
from app.services.run_service import RunService, run_service

logger = get_logger(__name__)

HEARTBEAT_INTERVAL_S = 15.0
SSE_POLL_TIMEOUT_S = 1.0


class _RunEventBus:
    """In-process pub/sub for live SSE subscribers (one queue per connection)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[UUID, list[queue.Queue]] = {}

    def subscribe(self, run_id: UUID) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: UUID, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(run_id) or []
            if q in subs:
                subs.remove(q)
            if not subs and run_id in self._subscribers:
                del self._subscribers[run_id]

    def publish(self, run_id: UUID, event: AgentEventResponse) -> None:
        with self._lock:
            subs = list(self._subscribers.get(run_id) or [])
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                logger.warning(
                    "SSE subscriber queue full run_id=%s — dropping live event id=%s",
                    run_id,
                    event.id,
                )


_event_bus = _RunEventBus()


def researcher_agent_name(index: int) -> str:
    labels = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")
    if 0 <= index < len(labels):
        return f"researcher-{labels[index]}"
    return f"researcher-{index + 1}"


class AgentEventService:
    """
    Emit once → persist + broadcast.
    Auth-ready: optional user_id enforces run ownership.
    """

    def __init__(
        self,
        runs: RunService | None = None,
        events: AgentEventsRepository | None = None,
        bus: _RunEventBus | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._events = events or agent_events_repository
        self._bus = bus or _event_bus

    def emit(
        self,
        *,
        run_id: UUID,
        agent_name: str,
        agent_role: AgentRole | str,
        event_type: AgentEventType | str,
        payload: dict[str, Any] | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        event_id: UUID | None = None,
    ) -> AgentEventResponse:
        role = AgentRole(agent_role) if isinstance(agent_role, str) else agent_role
        etype = AgentEventType(event_type) if isinstance(event_type, str) else event_type
        row = {
            "id": str(event_id or uuid4()),
            "run_id": str(run_id),
            "agent_name": agent_name.strip()[:100],
            "agent_role": role.value,
            "event_type": etype.value,
            "payload_json": payload or {},
            "tokens_in": max(0, int(tokens_in)),
            "tokens_out": max(0, int(tokens_out)),
            "cost_usd": max(0.0, float(cost_usd)),
        }
        saved = self._events.append(row)
        self._bus.publish(run_id, saved)
        if float(cost_usd) > 0:
            try:
                from app.core.run_control import run_control

                run_control.add_cost(run_id, float(cost_usd))
            except Exception:  # noqa: BLE001
                pass
        logger.debug(
            "agent_event run=%s agent=%s type=%s id=%s",
            run_id,
            agent_name,
            etype.value,
            saved.id,
        )
        return saved

    def list_events(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
        event_type: Optional[AgentEventType] = None,
        agent_name: Optional[str] = None,
        after_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AgentEventListResponse:
        self._authorized_run(run_id, user_id=user_id)
        items, total = self._events.list_for_run(
            run_id,
            event_type=event_type,
            agent_name=agent_name,
            after_id=after_id,
            limit=limit,
            offset=offset,
        )
        return AgentEventListResponse(
            items=items, total=total, limit=limit, offset=offset
        )

    def list_agents(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> AgentListResponse:
        summary = self.get_agents_status(run_id, user_id=user_id)
        return AgentListResponse(items=summary.agents, total=len(summary.agents))

    def get_agent(
        self,
        run_id: UUID,
        agent_name: str,
        *,
        user_id: Optional[UUID] = None,
    ) -> AgentStatusResponse:
        summary = self.get_agents_status(run_id, user_id=user_id)
        for agent in summary.agents:
            if agent.agent_name == agent_name:
                return agent
        raise NotFoundError(
            "Agent not found for this run",
            detail={"run_id": str(run_id), "agent_name": agent_name},
        )

    def get_agents_status(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> AgentsStatusSummaryResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        events = self._events.list_all_chronological(run.id)
        by_agent: dict[str, list[AgentEventResponse]] = {}
        for event in events:
            by_agent.setdefault(event.agent_name, []).append(event)

        agents: list[AgentStatusResponse] = []
        for name, evts in sorted(by_agent.items(), key=lambda x: x[1][0].created_at):
            agents.append(self._summarize_agent(name, evts))

        return AgentsStatusSummaryResponse(
            run_id=run.id,
            run_status=run.status.value,
            agents=agents,
            total_events=len(events),
            total_tokens_in=sum(a.tokens_in for a in agents),
            total_tokens_out=sum(a.tokens_out for a in agents),
            total_cost_usd=round(sum(a.cost_usd for a in agents), 6),
        )

    def stream_sse(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
        after_id: Optional[UUID] = None,
        replay: bool = True,
        heartbeat_s: float = HEARTBEAT_INTERVAL_S,
        is_disconnected: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        """
        Yield SSE frames. On connect:
        - optionally replay historical events (after after_id, no duplicates)
        - then live events from the bus
        - heartbeat comments while idle
        """
        self._authorized_run(run_id, user_id=user_id)
        seen: set[UUID] = set()
        last_id = after_id

        if replay:
            items, _ = self._events.list_for_run(
                run_id, after_id=after_id, limit=200, offset=0
            )
            # If after_id path returned empty because repo doesn't filter well,
            # fall back to full chronological filter in-memory.
            if after_id is not None and not items:
                all_items = self._events.list_all_chronological(run_id)
                items = self._events_after(all_items, after_id)
            for event in items:
                if event.id in seen:
                    continue
                seen.add(event.id)
                last_id = event.id
                yield self._format_sse(event)

        q = self._bus.subscribe(run_id)
        last_heartbeat = time.monotonic()
        try:
            while True:
                if is_disconnected and is_disconnected():
                    break
                try:
                    event = q.get(timeout=SSE_POLL_TIMEOUT_S)
                except queue.Empty:
                    now = time.monotonic()
                    if now - last_heartbeat >= heartbeat_s:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    continue

                if event.id in seen:
                    continue
                # Ignore events that are not strictly after last replayed cursor
                if last_id is not None and event.id == last_id:
                    continue
                seen.add(event.id)
                last_id = event.id
                yield self._format_sse(event)
                last_heartbeat = time.monotonic()
        finally:
            self._bus.unsubscribe(run_id, q)

    @staticmethod
    def _format_sse(event: AgentEventResponse) -> str:
        payload = event.model_dump(mode="json")
        data = json.dumps(payload, separators=(",", ":"), default=str)
        return f"id: {event.id}\nevent: agent_event\ndata: {data}\n\n"

    @staticmethod
    def _events_after(
        events: list[AgentEventResponse], after_id: UUID
    ) -> list[AgentEventResponse]:
        idx = next((i for i, e in enumerate(events) if e.id == after_id), None)
        if idx is None:
            raise ValidationAppError(
                "after_id not found in event history for this run",
                detail={"after_id": str(after_id)},
            )
        return events[idx + 1 :]

    @staticmethod
    def _summarize_agent(
        name: str, events: list[AgentEventResponse]
    ) -> AgentStatusResponse:
        role = events[0].agent_role
        tokens_in = sum(e.tokens_in for e in events)
        tokens_out = sum(e.tokens_out for e in events)
        cost = round(sum(e.cost_usd for e in events), 6)
        last = events[-1]
        error = None
        status = AgentRuntimeStatus.idle

        started = any(
            e.event_type
            in {
                AgentEventType.agent_started,
                AgentEventType.writer_started,
                AgentEventType.verifier_started,
            }
            for e in events
        )
        finished = any(
            e.event_type
            in {
                AgentEventType.agent_finished,
                AgentEventType.writer_finished,
                AgentEventType.verifier_finished,
            }
            for e in events
        )
        errored = any(e.event_type == AgentEventType.error for e in events)

        if errored and not finished:
            status = AgentRuntimeStatus.failed
            err_evt = next(
                e for e in reversed(events) if e.event_type == AgentEventType.error
            )
            error = str((err_evt.payload_json or {}).get("error") or "error")
        elif finished:
            status = AgentRuntimeStatus.completed
        elif started or any(
            e.event_type
            in {AgentEventType.section_started, AgentEventType.section_completed}
            for e in events
        ):
            status = AgentRuntimeStatus.running
        else:
            status = AgentRuntimeStatus.pending

        action = None
        if last.event_type == AgentEventType.thinking:
            action = str((last.payload_json or {}).get("message") or "thinking")
        elif last.event_type == AgentEventType.tool_call:
            action = f"tool:{ (last.payload_json or {}).get('tool') }"
        elif last.event_type == AgentEventType.tool_result:
            action = f"result:{ (last.payload_json or {}).get('tool') }"
        elif last.event_type == AgentEventType.section_started:
            action = f"writing:{ (last.payload_json or {}).get('title') or (last.payload_json or {}).get('section') }"
        elif last.event_type == AgentEventType.section_completed:
            action = f"wrote:{ (last.payload_json or {}).get('title') or (last.payload_json or {}).get('section') }"
        elif last.payload_json.get("message"):
            action = str(last.payload_json.get("message"))

        return AgentStatusResponse(
            agent_name=name,
            agent_role=role,
            status=status,
            last_event_type=last.event_type,
            last_event_at=last.created_at,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            event_count=len(events),
            error=error,
            current_action=action,
        )

    def _authorized_run(self, run_id: UUID, *, user_id: Optional[UUID]):
        run = self._runs.get_run(run_id)
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run


agent_event_service = AgentEventService()
