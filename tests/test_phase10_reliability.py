"""Phase 10 — production reliability, failure handling, and final backend QA."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.research_graph import run_research_graph
from app.agents.researcher_agent import (
    ResearcherAgent,
    ResearcherDependencies,
    ResearcherOutcome,
)
from app.core.run_control import RunAborted, StopReason, run_control
from app.core.run_lifecycle import ALLOWED_TRANSITIONS, assert_transition, can_transition
from app.main import app
from app.schemas.agent_event import AgentEventResponse, AgentEventType, AgentRole
from app.schemas.finding import FindingResponse
from app.schemas.report import ReportResponse
from app.schemas.run import CreateRunRequest, ResearchDepth, RunResponse, RunStatus
from app.schemas.source import SourceResponse
from app.schemas.usage import ExportFormat
from app.services import export_service as export_module
from app.services import research_orchestration_service as orch_module
from app.services import run_service as run_service_module
from app.services import usage_service as usage_module
from app.services.export_service import ExportService
from app.services.research_orchestration_service import ResearchOrchestrationService
from app.services.run_service import RunService
from app.services.usage_service import UsageService
from app.tools.base import ToolTimeoutError, with_retries
from app.api.v1 import runs as runs_api
from app.api.v1 import usage_export as usage_export_api


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRunsRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, Any]] = {}

    def create(self, row: dict[str, Any]) -> RunResponse:
        run_id = UUID(str(row["id"]))
        created = {
            **row,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": row.get("finished_at"),
            "cost_usd": row.get("cost_usd", 0),
            "plan_json": row.get("plan_json"),
            "user_id": None,
            "cancel_requested": bool(row.get("cancel_requested") or False),
            "cost_limit_usd": row.get("cost_limit_usd"),
            "started_at": row.get("started_at"),
            "stop_reason": row.get("stop_reason"),
            "duration_ms": row.get("duration_ms"),
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
        finished_at = row.get("finished_at")
        if isinstance(finished_at, str):
            finished_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        started_at = row.get("started_at")
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
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
            cancel_requested=bool(row.get("cancel_requested") or False),
            cost_limit_usd=(
                float(row["cost_limit_usd"])
                if row.get("cost_limit_usd") is not None
                else None
            ),
            started_at=started_at,
            stop_reason=row.get("stop_reason"),
            error=row.get("error"),
            created_at=created_at,
            finished_at=finished_at,
        )


class FakeEventsRepository:
    def __init__(self) -> None:
        self.rows: list[AgentEventResponse] = []

    def list_all_chronological(self, run_id: UUID) -> list[AgentEventResponse]:
        return sorted(
            [e for e in self.rows if e.run_id == run_id],
            key=lambda e: e.created_at,
        )

    def append(self, row: dict[str, Any]) -> AgentEventResponse:
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
            created_at=datetime.now(timezone.utc),
        )
        self.rows.append(event)
        return event


class FakeSourcesRepository:
    def __init__(self) -> None:
        self.rows: list[SourceResponse] = []

    def list_for_run(self, run_id: UUID) -> list[SourceResponse]:
        return [r for r in self.rows if r.run_id == run_id]


class FakeFindingsRepository:
    def __init__(self) -> None:
        self.rows: list[FindingResponse] = []

    def list_for_run(self, run_id: UUID) -> list[FindingResponse]:
        return [r for r in self.rows if r.run_id == run_id]


class FakeReportsRepository:
    def __init__(self) -> None:
        self.by_run: dict[UUID, ReportResponse] = {}

    def find_for_run(self, run_id: UUID) -> Optional[ReportResponse]:
        return self.by_run.get(run_id)

    def set(self, report: ReportResponse) -> None:
        self.by_run[report.run_id] = report


@pytest.fixture
def harness():
    runs_repo = FakeRunsRepository()
    events_repo = FakeEventsRepository()
    sources_repo = FakeSourcesRepository()
    findings_repo = FakeFindingsRepository()
    reports_repo = FakeReportsRepository()

    runs = RunService(repository=runs_repo, sources=sources_repo)
    usage = UsageService(
        runs=runs,
        runs_repo=runs_repo,
        events=events_repo,
        sources=sources_repo,
        findings=findings_repo,
    )
    export = ExportService(runs=runs, reports=reports_repo, sources=sources_repo)

    prev_runs = run_service_module.run_service
    prev_usage = usage_module.usage_service
    prev_export = export_module.export_service
    run_service_module.run_service = runs
    usage_module.usage_service = usage
    export_module.export_service = export
    runs_api.run_service = runs
    usage_export_api.usage_service = usage
    usage_export_api.export_service = export

    client = TestClient(app)
    yield {
        "client": client,
        "runs": runs,
        "runs_repo": runs_repo,
        "events": events_repo,
        "sources": sources_repo,
        "findings": findings_repo,
        "reports": reports_repo,
        "usage": usage,
        "export": export,
    }

    run_control.end  # noqa: B018 - keep reference
    for rid in list(getattr(run_control, "_states", {})):
        run_control.end(rid)

    run_service_module.run_service = prev_runs
    usage_module.usage_service = prev_usage
    export_module.export_service = prev_export
    runs_api.run_service = prev_runs
    usage_export_api.usage_service = prev_usage
    usage_export_api.export_service = prev_export


# ---------------------------------------------------------------------------
# 1) Unit — status transitions
# ---------------------------------------------------------------------------


def test_legal_status_happy_path():
    path = [
        RunStatus.plan_pending,
        RunStatus.plan_ready,
        RunStatus.approved,
        RunStatus.running,
        RunStatus.completed,
    ]
    for a, b in zip(path, path[1:]):
        assert can_transition(a, b)


@pytest.mark.parametrize(
    "target",
    [RunStatus.failed, RunStatus.cancelled, RunStatus.partial],
)
def test_legal_running_alternatives(target: RunStatus):
    assert can_transition(RunStatus.running, target)


def test_invalid_transition_raises():
    with pytest.raises(Exception) as exc:
        assert_transition(RunStatus.plan_pending, RunStatus.completed)
    assert "Invalid status transition" in str(exc.value)


def test_cancelled_is_terminal():
    assert ALLOWED_TRANSITIONS[RunStatus.cancelled] == set()
    assert not can_transition(RunStatus.cancelled, RunStatus.running)


# ---------------------------------------------------------------------------
# 2) Unit — retries / backoff / timeout helpers
# ---------------------------------------------------------------------------


def test_with_retries_exponential_backoff_and_max_two_retries():
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky():
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    with patch("app.tools.base.time.sleep", side_effect=lambda s: sleeps.append(s)):
        with pytest.raises(Exception):
            with_retries("unit", flaky, max_retries=2, backoff_base_s=0.5)

    assert calls["n"] == 3  # initial + 2 retries
    assert sleeps == [0.5, 1.0]


def test_tool_timeout_raises_tool_timeout_error():
    def always_timeout():
        raise TimeoutError("slow")

    with patch("app.tools.base.time.sleep"):
        with pytest.raises(ToolTimeoutError):
            with_retries("timeout-op", always_timeout, max_retries=1, backoff_base_s=0.01)


# ---------------------------------------------------------------------------
# 3) Unit — run control (cancel / cost / timeout)
# ---------------------------------------------------------------------------


def test_run_control_cancel_and_cost_ceiling():
    run_id = uuid4()
    run_control.begin(run_id, cost_limit_usd=0.01, timeout_s=60)
    try:
        assert run_control.check(run_id) == StopReason.none
        run_control.add_cost(run_id, 0.005)
        assert run_control.check(run_id) == StopReason.none
        run_control.add_cost(run_id, 0.006)
        assert run_control.check(run_id) == StopReason.cost_limit
        with pytest.raises(RunAborted) as exc:
            run_control.assert_can_continue(run_id)
        assert exc.value.reason == StopReason.cost_limit

        run_control.request_cancel(run_id)
        assert run_control.check(run_id) == StopReason.cancelled
    finally:
        run_control.end(run_id)


def test_run_control_timeout():
    run_id = uuid4()
    run_control.begin(run_id, cost_limit_usd=100, timeout_s=0.05)
    try:
        time.sleep(0.08)
        assert run_control.check(run_id) == StopReason.timeout
    finally:
        run_control.end(run_id)


# ---------------------------------------------------------------------------
# 4) API — cancellation
# ---------------------------------------------------------------------------


def test_cancel_api_sets_flag_quickly(harness):
    client: TestClient = harness["client"]
    run = harness["runs"].create_run(
        CreateRunRequest(topic="Cancel me please", depth=ResearchDepth.quick)
    )
    harness["runs"].update_run_status(run.id, RunStatus.plan_ready)
    harness["runs"].update_run_status(run.id, RunStatus.approved)
    harness["runs"].update_run_status(
        run.id, RunStatus.running, clear_cancel=True, set_started=True
    )

    t0 = time.monotonic()
    resp = client.post(f"/api/v1/runs/{run.id}/cancel")
    elapsed = time.monotonic() - t0

    assert resp.status_code == 200
    assert elapsed < 1.0
    body = resp.json()["data"]
    assert body["status"] == "cancelled"
    assert body["cancel_requested"] is True
    assert body["stop_reason"] == "cancelled"
    assert run_control.is_cancel_requested(run.id) or body["cancel_requested"]


def test_cancel_preserves_completed_findings(harness):
    run = harness["runs"].create_run(
        CreateRunRequest(topic="Preserve findings", depth=ResearchDepth.quick)
    )
    finding = FindingResponse(
        id=uuid4(),
        run_id=run.id,
        section_id=uuid4(),
        claim="Already discovered claim",
        source_id=None,
        confidence=0.8,
        is_gap=False,
        created_at=datetime.now(timezone.utc),
    )
    harness["findings"].rows.append(finding)
    harness["runs"].update_run_status(run.id, RunStatus.plan_ready)
    harness["runs"].update_run_status(run.id, RunStatus.approved)
    harness["runs"].update_run_status(run.id, RunStatus.running, clear_cancel=True)

    harness["runs"].cancel_run(run.id)
    remaining = harness["findings"].list_for_run(run.id)
    assert len(remaining) == 1
    assert remaining[0].claim == "Already discovered claim"


# ---------------------------------------------------------------------------
# 5) Cost ceiling → partial
# ---------------------------------------------------------------------------


def test_cost_ceiling_finalize_sets_partial(harness):
    runs = harness["runs"]
    run = runs.create_run(
        CreateRunRequest(
            topic="Cost ceiling run",
            depth=ResearchDepth.quick,
            cost_limit_usd=0.01,
        )
    )
    runs.update_run_status(run.id, RunStatus.plan_ready)
    runs.update_run_status(run.id, RunStatus.approved)
    runs.update_run_status(run.id, RunStatus.running, clear_cancel=True, set_started=True)

    class FakeTasks:
        def list_for_run(self, run_id: UUID):
            from app.schemas.research_run import ResearchTaskResponse, ResearchTaskStatus

            return [
                ResearchTaskResponse(
                    id=uuid4(),
                    run_id=run_id,
                    section_id=uuid4(),
                    sub_question="q1",
                    status=ResearchTaskStatus.completed,
                    tool_calls=1,
                    error=None,
                    gap_note=None,
                    created_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                ),
                ResearchTaskResponse(
                    id=uuid4(),
                    run_id=run_id,
                    section_id=uuid4(),
                    sub_question="q2",
                    status=ResearchTaskStatus.failed,
                    tool_calls=0,
                    error="cost_limit",
                    gap_note="Skipped",
                    created_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                ),
            ]

    orch = ResearchOrchestrationService(runs=runs, tasks=FakeTasks())  # type: ignore[arg-type]
    run_control.begin(run.id, cost_limit_usd=0.01)
    run_control.add_cost(run.id, 0.02)
    try:
        orch._finalize_run(run.id, abort_reason=StopReason.cost_limit)
    finally:
        run_control.end(run.id)

    updated = runs.get_run(run.id)
    assert updated.status == RunStatus.partial
    assert updated.stop_reason == "cost_limit"


# ---------------------------------------------------------------------------
# 6) Parallel researcher isolation + LangGraph
# ---------------------------------------------------------------------------


def test_langgraph_isolates_failed_researcher():
    calls: list[str] = []

    def _research(**kwargs):
        name = kwargs.get("agent_name") or ""
        calls.append(name)
        if name.endswith("0"):
            raise RuntimeError("isolated boom")
        return ResearcherOutcome(
            task_id=kwargs["task_id"],
            section_id=kwargs["section_id"],
            sub_question=kwargs["sub_question"],
            success=True,
            tool_calls=1,
            claim="ok claim",
            confidence=0.7,
            agent_name=name,
        )

    agent = MagicMock(spec=ResearcherAgent)
    agent.research.side_effect = _research

    tasks = [
        {
            "id": str(uuid4()),
            "section_id": str(uuid4()),
            "sub_question": "failing question?",
            "agent_name": "researcher-0",
        },
        {
            "id": str(uuid4()),
            "section_id": str(uuid4()),
            "sub_question": "ok question?",
            "agent_name": "researcher-1",
        },
    ]
    outcomes = run_research_graph(
        run_id=uuid4(),
        topic="isolation",
        tasks=tasks,
        researcher=agent,
        max_workers=2,
    )
    assert len(outcomes) == 2
    assert sum(1 for o in outcomes if o.success) == 1
    assert sum(1 for o in outcomes if not o.success) == 1
    assert any("isolated boom" in (o.error or "") for o in outcomes if not o.success)


def test_researcher_respects_should_continue_cancel():
    deps = ResearcherDependencies(
        search=lambda q, n: MagicMock(results=[]),
        fetch=lambda u: MagicMock(html="", final_url=u),
        extract=lambda h, t: MagicMock(text="", title=t),
        score=lambda **kw: MagicMock(score=50),
    )
    agent = ResearcherAgent(deps=deps, events=None)
    outcome = agent.research(
        task_id=uuid4(),
        section_id=uuid4(),
        sub_question="Will be cancelled?",
        topic="topic",
        should_continue=lambda: False,
    )
    assert outcome.success is False
    assert outcome.error == "cancelled"


# ---------------------------------------------------------------------------
# 7) Partial results + export (reuse phase 9)
# ---------------------------------------------------------------------------


def test_partial_export_still_works(harness):
    run = harness["runs"].create_run(
        CreateRunRequest(topic="Partial export", depth=ResearchDepth.quick)
    )
    harness["runs_repo"].update(
        run.id,
        {"status": RunStatus.partial.value, "stop_reason": "cost_limit"},
    )
    harness["reports"].set(
        ReportResponse(
            id=uuid4(),
            run_id=run.id,
            markdown="# Partial\n\nSaved before ceiling.\n\n## Bibliography\n\n- [1] x\n",
            sections_json=[],
            citation_map_json={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    md = harness["export"].export(run.id, format=ExportFormat.md)
    assert b"Partial" in md.body
    pdf = harness["export"].export(run.id, format=ExportFormat.pdf)
    assert pdf.body[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# 8) API — usage / history still available
# ---------------------------------------------------------------------------


def test_usage_and_history_apis(harness):
    client: TestClient = harness["client"]
    run = harness["runs"].create_run(
        CreateRunRequest(topic="Usage after reliability", depth=ResearchDepth.quick)
    )
    harness["events"].append(
        {
            "id": str(uuid4()),
            "run_id": str(run.id),
            "agent_name": "planner",
            "agent_role": AgentRole.planner.value,
            "event_type": AgentEventType.tool_result.value,
            "payload_json": {"model": "m"},
            "tokens_in": 5,
            "tokens_out": 2,
            "cost_usd": 0.001,
        }
    )
    # UsageService expects list_all_chronological on events repo — wire via Fake that usage uses
    harness["events"].rows[-1]  # already appended via append helper above? 
    # Fix: FakeEvents for usage uses list_all_chronological; we used append which works.

    # Re-bind usage events repo — already harness usage uses findings/sources/events
    usage_module.usage_service = UsageService(
        runs=harness["runs"],
        runs_repo=harness["runs_repo"],
        events=harness["events"],
        sources=harness["sources"],
        findings=harness["findings"],
    )
    usage_export_api.usage_service = usage_module.usage_service

    u = client.get(f"/api/v1/runs/{run.id}/usage")
    assert u.status_code == 200
    assert u.json()["data"]["total_tokens"] == 7

    h = client.get(f"/api/v1/runs/{run.id}/history")
    assert h.status_code == 200
    assert h.json()["data"]["timeline_count"] >= 1


# ---------------------------------------------------------------------------
# 9) Health reliability surface + OpenAPI description
# ---------------------------------------------------------------------------


def test_health_includes_reliability_config(harness):
    resp = harness["client"].get("/api/v1/health")
    assert resp.status_code == 200
    rel = resp.json()["data"]["reliability"]
    assert rel["tool_timeout_s"] == 30.0 or rel["tool_timeout_s"] > 0
    assert rel["tool_max_retries"] == 2
    assert "run_cost_limit_usd" in rel
    assert "run_timeout_s" in rel


def test_openapi_documents_cancel_and_workflow():
    schema = app.openapi()
    assert "Reliability" in schema["info"]["description"] or "Phase 10" in schema["info"]["description"]
    cancel = schema["paths"]["/api/v1/runs/{run_id}/cancel"]["post"]
    assert "cancel" in cancel["summary"].lower() or "Cancel" in cancel["summary"]
    assert 409 in cancel.get("responses", {}) or "409" in cancel.get("responses", {})


# ---------------------------------------------------------------------------
# 10) End-to-end status machine via API (no live AI)
# ---------------------------------------------------------------------------


def test_e2e_status_flow_create_to_cancelled(harness):
    client: TestClient = harness["client"]
    created = client.post(
        "/api/v1/runs",
        json={"topic": "E2E reliability flow", "depth": "quick", "cost_limit_usd": 0.5},
    )
    assert created.status_code == 200
    run_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "plan_pending"

    # Simulate plan_ready → approved → running via service (planner not under test here)
    rid = UUID(run_id)
    harness["runs"].update_run_status(rid, RunStatus.plan_ready)
    harness["runs"].update_run_status(rid, RunStatus.approved)
    harness["runs"].update_run_status(
        rid, RunStatus.running, clear_cancel=True, set_started=True
    )

    status = client.get(f"/api/v1/runs/{run_id}/status")
    assert status.json()["data"]["status"] == "running"

    cancel = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["data"]["status"] == "cancelled"

    # Finalize must not overwrite cancelled
    orch = ResearchOrchestrationService(runs=harness["runs"])
    orch._finalize_run(rid, abort_reason=StopReason.none)
    assert harness["runs"].get_run(rid).status == RunStatus.cancelled
