"""Phase 6 agent events, observability, and SSE tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import agents_events as agents_events_api
from app.main import app
from app.schemas.agent_event import (
    AgentEventResponse,
    AgentEventType,
    AgentRole,
)
from app.schemas.run import CreateRunRequest, ResearchDepth, RunResponse, RunStatus
from app.services import agent_event_service as events_module
from app.services import run_service as run_service_module
from app.services.agent_event_service import AgentEventService, _RunEventBus
from app.services.run_service import RunService


class FakeRunsRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, Any]] = {}

    def create(self, row: dict[str, Any]) -> RunResponse:
        run_id = UUID(str(row["id"]))
        created = {
            **row,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "cost_usd": 0,
            "plan_json": None,
            "user_id": None,
        }
        self.rows[run_id] = created
        return self._to(created)

    def get_by_id(self, run_id: UUID) -> Optional[RunResponse]:
        row = self.rows.get(run_id)
        return self._to(row) if row else None

    def list(self, **kwargs: Any) -> tuple[list[RunResponse], int]:
        items = [self._to(r) for r in self.rows.values()]
        return items, len(items)

    def update(self, run_id: UUID, fields: dict[str, Any]) -> RunResponse:
        self.rows[run_id] = {**self.rows[run_id], **fields}
        return self._to(self.rows[run_id])

    def delete(self, run_id: UUID) -> None:
        self.rows.pop(run_id, None)

    @staticmethod
    def _to(row: dict[str, Any]) -> RunResponse:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return RunResponse(
            id=UUID(str(row["id"])),
            user_id=None,
            topic=row["topic"],
            depth=ResearchDepth(row["depth"]),
            audience=row["audience"],
            tone=row["tone"],
            target_length=int(row["target_length"]),
            source_types=list(row.get("source_types") or []),
            status=RunStatus(row["status"]),
            plan_json=row.get("plan_json"),
            cost_usd=float(row.get("cost_usd") or 0),
            duration_ms=row.get("duration_ms"),
            error=row.get("error"),
            created_at=created_at,
            finished_at=None,
        )


class FakeAgentEventsRepository:
    def __init__(self) -> None:
        self.rows: list[AgentEventResponse] = []

    def table_exists(self) -> bool:
        return True

    def append(self, row: dict[str, Any]) -> AgentEventResponse:
        # Tiny sleep to keep created_at ordering stable under fast inserts.
        now = datetime.now(timezone.utc)
        if self.rows:
            prev = self.rows[-1].created_at
            if now <= prev:
                from datetime import timedelta

                now = prev + timedelta(microseconds=1)
        event = AgentEventResponse(
            id=UUID(str(row["id"])),
            run_id=UUID(str(row["run_id"])),
            agent_name=row["agent_name"],
            agent_role=AgentRole(row["agent_role"]),
            event_type=AgentEventType(row["event_type"]),
            payload_json=row.get("payload_json") or {},
            tokens_in=int(row.get("tokens_in") or 0),
            tokens_out=int(row.get("tokens_out") or 0),
            cost_usd=float(row.get("cost_usd") or 0),
            created_at=now,
        )
        self.rows.append(event)
        return event

    def get_by_id(self, run_id: UUID, event_id: UUID) -> AgentEventResponse:
        for row in self.rows:
            if row.run_id == run_id and row.id == event_id:
                return row
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Agent event not found")

    def list_for_run(
        self,
        run_id: UUID,
        *,
        event_type: AgentEventType | None = None,
        agent_name: str | None = None,
        after_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AgentEventResponse], int]:
        items = [r for r in self.rows if r.run_id == run_id]
        if event_type is not None:
            items = [r for r in items if r.event_type == event_type]
        if agent_name:
            items = [r for r in items if r.agent_name == agent_name]
        items.sort(key=lambda r: (r.created_at, str(r.id)))
        if after_id is not None:
            idx = next((i for i, r in enumerate(items) if r.id == after_id), None)
            if idx is None:
                self.get_by_id(run_id, after_id)
                items = []
            else:
                items = items[idx + 1 :]
        total = len(items)
        return items[offset : offset + limit], total

    def list_all_chronological(self, run_id: UUID) -> list[AgentEventResponse]:
        items, _ = self.list_for_run(run_id, limit=10_000, offset=0)
        return items

    def count_for_run(self, run_id: UUID) -> int:
        return len([r for r in self.rows if r.run_id == run_id])


@pytest.fixture
def harness():
    runs_repo = FakeRunsRepository()
    events_repo = FakeAgentEventsRepository()
    bus = _RunEventBus()
    runs = RunService(repository=runs_repo)
    events = AgentEventService(runs=runs, events=events_repo, bus=bus)

    original = {
        "run": run_service_module.run_service,
        "events": events_module.agent_event_service,
        "api": agents_events_api.agent_event_service,
    }
    run_service_module.run_service = runs
    events_module.agent_event_service = events
    agents_events_api.agent_event_service = events

    client = TestClient(app)

    def seed_run() -> RunResponse:
        return runs.create_run(CreateRunRequest(topic="AI observability testing topic"))

    yield {
        "client": client,
        "runs": runs,
        "events": events,
        "events_repo": events_repo,
        "bus": bus,
        "seed": seed_run,
    }

    run_service_module.run_service = original["run"]
    events_module.agent_event_service = original["events"]
    agents_events_api.agent_event_service = original["api"]


def test_event_persistence_and_ordering(harness):
    run = harness["seed"]()
    svc: AgentEventService = harness["events"]

    e1 = svc.emit(
        run_id=run.id,
        agent_name="planner-01",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.agent_started,
        payload={"message": "start"},
    )
    e2 = svc.emit(
        run_id=run.id,
        agent_name="planner-01",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.thinking,
        payload={"message": "thinking"},
    )
    e3 = svc.emit(
        run_id=run.id,
        agent_name="researcher-alpha",
        agent_role=AgentRole.researcher,
        event_type=AgentEventType.tool_call,
        payload={"tool": "search"},
    )

    listed = svc.list_events(run.id, limit=50, offset=0)
    assert listed.total == 3
    assert [i.id for i in listed.items] == [e1.id, e2.id, e3.id]
    assert listed.items[0].created_at <= listed.items[1].created_at <= listed.items[2].created_at


def test_event_filtering_and_pagination(harness):
    run = harness["seed"]()
    svc: AgentEventService = harness["events"]
    for i in range(5):
        svc.emit(
            run_id=run.id,
            agent_name="planner-01" if i % 2 == 0 else "researcher-alpha",
            agent_role=AgentRole.planner if i % 2 == 0 else AgentRole.researcher,
            event_type=AgentEventType.thinking if i < 4 else AgentEventType.error,
            payload={"i": i, "error": "boom"} if i == 4 else {"i": i},
        )

    page = svc.list_events(run.id, agent_name="planner-01", limit=10, offset=0)
    assert page.total == 3
    assert all(i.agent_name == "planner-01" for i in page.items)

    errors = svc.list_events(run.id, event_type=AgentEventType.error)
    assert errors.total == 1
    assert errors.items[0].event_type == AgentEventType.error


def test_replay_from_after_id(harness):
    run = harness["seed"]()
    svc: AgentEventService = harness["events"]
    ids = []
    for i in range(4):
        ev = svc.emit(
            run_id=run.id,
            agent_name="orchestrator",
            agent_role=AgentRole.orchestrator,
            event_type=AgentEventType.thinking,
            payload={"i": i},
        )
        ids.append(ev.id)

    after = svc.list_events(run.id, after_id=ids[1], limit=50)
    assert [e.id for e in after.items] == ids[2:]


def test_agents_status_summary(harness):
    run = harness["seed"]()
    svc: AgentEventService = harness["events"]
    svc.emit(
        run_id=run.id,
        agent_name="planner-01",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.agent_started,
        payload={},
    )
    svc.emit(
        run_id=run.id,
        agent_name="planner-01",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.agent_finished,
        payload={"success": True},
        tokens_in=10,
        tokens_out=20,
        cost_usd=0.01,
    )
    svc.emit(
        run_id=run.id,
        agent_name="researcher-alpha",
        agent_role=AgentRole.researcher,
        event_type=AgentEventType.agent_started,
        payload={},
    )
    svc.emit(
        run_id=run.id,
        agent_name="researcher-alpha",
        agent_role=AgentRole.researcher,
        event_type=AgentEventType.error,
        payload={"error": "search boom"},
    )

    status = svc.get_agents_status(run.id)
    by_name = {a.agent_name: a for a in status.agents}
    assert by_name["planner-01"].status.value == "completed"
    assert by_name["researcher-alpha"].status.value == "failed"
    assert status.total_tokens_in == 10
    assert status.total_tokens_out == 20


def test_api_events_and_agents(harness):
    client: TestClient = harness["client"]
    run = harness["seed"]()
    svc: AgentEventService = harness["events"]
    svc.emit(
        run_id=run.id,
        agent_name="planner-01",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.agent_started,
        payload={"message": "hi"},
        tokens_in=5,
    )

    events = client.get(f"/api/v1/runs/{run.id}/events")
    assert events.status_code == 200
    body = events.json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "agent_started"

    agents = client.get(f"/api/v1/runs/{run.id}/agents")
    assert agents.status_code == 200
    assert agents.json()["data"]["total"] == 1

    status = client.get(f"/api/v1/runs/{run.id}/agents/status")
    assert status.status_code == 200
    assert status.json()["data"]["total_events"] == 1

    one = client.get(f"/api/v1/runs/{run.id}/agents/planner-01")
    assert one.status_code == 200
    assert one.json()["data"]["agent_name"] == "planner-01"


def test_sse_stream_replay_and_live(harness):
    run = harness["seed"]()
    svc: AgentEventService = harness["events"]

    first = svc.emit(
        run_id=run.id,
        agent_name="orchestrator",
        agent_role=AgentRole.orchestrator,
        event_type=AgentEventType.agent_started,
        payload={"message": "stored"},
    )

    live_q = harness["bus"].subscribe(run.id)
    live = svc.emit(
        run_id=run.id,
        agent_name="orchestrator",
        agent_role=AgentRole.orchestrator,
        event_type=AgentEventType.thinking,
        payload={"message": "live"},
    )
    delivered = live_q.get(timeout=1)
    harness["bus"].unsubscribe(run.id, live_q)
    assert delivered.id == live.id

    state = {"n": 0}

    def after_replay_disconnect() -> bool:
        state["n"] += 1
        return state["n"] > 5

    frames = list(
        svc.stream_sse(run.id, replay=True, is_disconnected=after_replay_disconnect)
    )
    joined = "".join(frames)
    assert "event: agent_event" in joined
    assert str(first.id) in joined
    assert str(live.id) in joined


def test_sse_reconnect_no_duplicates(harness):
    run = harness["seed"]()
    svc: AgentEventService = harness["events"]
    e1 = svc.emit(
        run_id=run.id,
        agent_name="orchestrator",
        agent_role=AgentRole.orchestrator,
        event_type=AgentEventType.agent_started,
        payload={"n": 1},
    )
    e2 = svc.emit(
        run_id=run.id,
        agent_name="orchestrator",
        agent_role=AgentRole.orchestrator,
        event_type=AgentEventType.thinking,
        payload={"n": 2},
    )

    state = {"n": 0}

    def stop_soon() -> bool:
        state["n"] += 1
        return state["n"] > 3

    frames = list(
        svc.stream_sse(run.id, after_id=e1.id, replay=True, is_disconnected=stop_soon)
    )
    joined = "".join(frames)
    assert str(e2.id) in joined
    event_ids = [
        line.split("id: ", 1)[1].strip()
        for line in joined.splitlines()
        if line.startswith("id: ")
    ]
    assert str(e1.id) not in event_ids
    assert str(e2.id) in event_ids


def test_sse_http_route_registered(harness):
    client: TestClient = harness["client"]
    paths = client.app.openapi()["paths"]
    assert "/api/v1/runs/{run_id}/stream" in paths
    methods = paths["/api/v1/runs/{run_id}/stream"]["get"]
    assert methods["responses"]


def test_error_event_api(harness):
    client: TestClient = harness["client"]
    run = harness["seed"]()
    harness["events"].emit(
        run_id=run.id,
        agent_name="researcher-beta",
        agent_role=AgentRole.researcher,
        event_type=AgentEventType.error,
        payload={"error": "tool failed"},
    )
    response = client.get(
        f"/api/v1/runs/{run.id}/events",
        params={"event_type": "error"},
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["payload_json"]["error"] == "tool failed"


def test_stream_sse_helper_replay(harness):
    run = harness["seed"]()
    svc: AgentEventService = harness["events"]
    e1 = svc.emit(
        run_id=run.id,
        agent_name="orchestrator",
        agent_role=AgentRole.orchestrator,
        event_type=AgentEventType.agent_started,
        payload={},
    )
    frames = []
    stopped = {"n": 0}

    def disconnect():
        stopped["n"] += 1
        return stopped["n"] > 1

    for frame in svc.stream_sse(run.id, replay=True, is_disconnected=disconnect):
        frames.append(frame)
    assert any(str(e1.id) in f for f in frames)
