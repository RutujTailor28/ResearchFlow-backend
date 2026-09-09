"""Phase 9 usage, cost, export, history, and timeline replay tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.agent_event import AgentEventResponse, AgentEventType, AgentRole
from app.schemas.report import ReportResponse
from app.schemas.run import CreateRunRequest, ResearchDepth, RunResponse, RunStatus
from app.schemas.source import SourceResponse
from app.schemas.usage import ExportFormat
from app.services import export_service as export_module
from app.services import run_service as run_service_module
from app.services import usage_service as usage_module
from app.services.export_service import ExportService
from app.services.run_service import RunService
from app.services.usage_service import UsageService
from app.api.v1 import runs as runs_api
from app.api.v1 import usage_export as usage_export_api


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

    def add(
        self,
        *,
        run_id: UUID,
        agent_name: str,
        agent_role: AgentRole,
        event_type: AgentEventType,
        payload: dict[str, Any] | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        created_at: datetime | None = None,
    ) -> AgentEventResponse:
        event = AgentEventResponse(
            id=uuid4(),
            run_id=run_id,
            agent_name=agent_name,
            agent_role=agent_role,
            event_type=event_type,
            payload_json=payload or {},
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            created_at=created_at or datetime.now(timezone.utc),
        )
        self.rows.append(event)
        return event


class FakeSourcesRepository:
    def __init__(self) -> None:
        self.rows: list[SourceResponse] = []

    def list_for_run(self, run_id: UUID) -> list[SourceResponse]:
        return [r for r in self.rows if r.run_id == run_id]

    def add(self, *, run_id: UUID, url: str, title: str) -> SourceResponse:
        source = SourceResponse(
            id=uuid4(),
            run_id=run_id,
            url=url,
            domain="example.com",
            title=title,
            published_at=None,
            quality_score=0.8,
            content_hash="hash",
            extract_text="excerpt",
            created_at=datetime.now(timezone.utc),
        )
        self.rows.append(source)
        return source


class FakeFindingsRepository:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    def list_for_run(self, run_id: UUID) -> list[Any]:
        return [r for r in self.rows if getattr(r, "run_id", None) == run_id]


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
        "reports": reports_repo,
        "usage": usage,
        "export": export,
    }

    run_service_module.run_service = prev_runs
    usage_module.usage_service = prev_usage
    export_module.export_service = prev_export
    runs_api.run_service = prev_runs
    usage_export_api.usage_service = prev_usage
    usage_export_api.export_service = prev_export


def _seed_run(harness: dict) -> RunResponse:
    run = harness["runs"].create_run(
        CreateRunRequest(topic="Usage tracking phase nine", depth=ResearchDepth.quick)
    )
    finished = datetime.now(timezone.utc)
    harness["runs_repo"].update(
        run.id,
        {
            "status": RunStatus.completed.value,
            "finished_at": finished.isoformat(),
        },
    )
    return harness["runs"].get_run(run.id)


def test_cost_aggregation_and_token_tracking_avoids_double_count(harness):
    run = _seed_run(harness)
    t0 = datetime.now(timezone.utc)
    harness["events"].add(
        run_id=run.id,
        agent_name="planner",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.tool_result,
        payload={"model": "openrouter/test", "tool": "openrouter.chat"},
        tokens_in=100,
        tokens_out=40,
        cost_usd=0.002,
        created_at=t0,
    )
    # Rollup on finished must not double-count when leaf exists.
    harness["events"].add(
        run_id=run.id,
        agent_name="planner",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.agent_finished,
        payload={"success": True},
        tokens_in=100,
        tokens_out=40,
        cost_usd=0.002,
        created_at=t0 + timedelta(seconds=1),
    )
    harness["events"].add(
        run_id=run.id,
        agent_name="writer",
        agent_role=AgentRole.writer,
        event_type=AgentEventType.section_completed,
        payload={"model": "openrouter/writer", "section": "intro"},
        tokens_in=200,
        tokens_out=80,
        cost_usd=0.005,
        created_at=t0 + timedelta(seconds=2),
    )
    harness["events"].add(
        run_id=run.id,
        agent_name="writer",
        agent_role=AgentRole.writer,
        event_type=AgentEventType.writer_finished,
        payload={"success": True},
        tokens_in=200,
        tokens_out=80,
        cost_usd=0.005,
        created_at=t0 + timedelta(seconds=3),
    )

    usage = harness["usage"].get_usage(run.id)
    assert usage.total_tokens_in == 300
    assert usage.total_tokens_out == 120
    assert usage.total_tokens == 420
    assert usage.total_cost_usd == pytest.approx(0.007)
    assert usage.cost_by_agent["planner"] == pytest.approx(0.002)
    assert usage.cost_by_agent["writer"] == pytest.approx(0.005)
    models = set(usage.models_used)
    assert "openrouter/test" in models
    assert "openrouter/writer" in models

    cost = harness["usage"].get_cost(run.id)
    assert cost.total_cost_usd == pytest.approx(0.007)
    assert cost.cost_by_role["planner"] == pytest.approx(0.002)
    assert cost.cost_by_role["writer"] == pytest.approx(0.005)

    # Snapshot onto run without inventing numbers.
    snap = harness["runs"].get_run(run.id)
    assert snap.cost_usd == pytest.approx(0.007)


def test_missing_provider_cost_is_not_invented(harness):
    run = _seed_run(harness)
    harness["events"].add(
        run_id=run.id,
        agent_name="planner",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.tool_result,
        payload={"model": "openrouter/test"},
        tokens_in=50,
        tokens_out=10,
        cost_usd=0.0,
    )
    usage = harness["usage"].get_usage(run.id)
    assert usage.total_tokens_in == 50
    assert usage.total_cost_usd == 0.0
    assert usage.cost_by_agent == {}


def test_markdown_pdf_docx_export(harness):
    run = _seed_run(harness)
    markdown = (
        "# Usage tracking phase nine\n\n"
        "## Findings\n\n"
        "Claim supported by evidence [1].\n\n"
        "## Bibliography\n\n"
        "- [1] Example Source — https://example.com\n"
    )
    harness["reports"].set(
        ReportResponse(
            id=uuid4(),
            run_id=run.id,
            markdown=markdown,
            sections_json=[],
            sections_confidence_json={},
            unverified_claims_json=[],
            citation_map_json={},
            conflicts_json=[],
            verification_json=None,
            verified_at=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )

    md = harness["export"].export(run.id, format=ExportFormat.md)
    assert md.media_type.startswith("text/markdown")
    assert b"Bibliography" in md.body

    pdf = harness["export"].export(run.id, format=ExportFormat.pdf)
    assert pdf.media_type == "application/pdf"
    assert pdf.body[:4] == b"%PDF"

    docx = harness["export"].export(run.id, format=ExportFormat.docx)
    assert "wordprocessingml" in docx.media_type
    assert docx.body[:2] == b"PK"  # zip/docx


def test_export_api_and_conflict_without_report(harness):
    client: TestClient = harness["client"]
    run = _seed_run(harness)

    missing = client.get(f"/api/v1/runs/{run.id}/export?format=md")
    assert missing.status_code == 409

    harness["reports"].set(
        ReportResponse(
            id=uuid4(),
            run_id=run.id,
            markdown="# Hello\n\nWorld\n",
            sections_json=[],
            sections_confidence_json={},
            unverified_claims_json=[],
            citation_map_json={},
            conflicts_json=[],
            verification_json=None,
            verified_at=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    ok = client.get(f"/api/v1/runs/{run.id}/export?format=md")
    assert ok.status_code == 200
    assert "Hello" in ok.text


def test_history_fields_and_timeline_replay_consistency(harness):
    client: TestClient = harness["client"]
    run = _seed_run(harness)
    harness["sources"].add(
        run_id=run.id, url="https://example.com/a", title="Source A"
    )
    harness["sources"].add(
        run_id=run.id, url="https://example.com/b", title="Source B"
    )
    t0 = datetime.now(timezone.utc)
    e1 = harness["events"].add(
        run_id=run.id,
        agent_name="planner",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.agent_started,
        payload={"operation": "plan"},
        created_at=t0,
    )
    e2 = harness["events"].add(
        run_id=run.id,
        agent_name="planner",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.tool_result,
        payload={"model": "m1"},
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.001,
        created_at=t0 + timedelta(milliseconds=50),
    )
    e3 = harness["events"].add(
        run_id=run.id,
        agent_name="planner",
        agent_role=AgentRole.planner,
        event_type=AgentEventType.agent_finished,
        payload={"success": True},
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.001,
        created_at=t0 + timedelta(milliseconds=100),
    )

    listed = client.get("/api/v1/runs")
    assert listed.status_code == 200
    item = next(i for i in listed.json()["data"]["items"] if i["id"] == str(run.id))
    assert item["topic"] == run.topic
    assert item["status"] == "completed"
    assert item["source_count"] == 2
    assert item["created_at"]
    assert item["finished_at"]
    assert "duration_ms" in item
    assert "cost_usd" in item

    hist = client.get(f"/api/v1/runs/{run.id}/history")
    assert hist.status_code == 200
    body = hist.json()["data"]
    assert body["run"]["topic"] == run.topic
    assert body["run"]["source_count"] == 2
    assert body["run"]["total_cost_usd"] == pytest.approx(0.001)
    assert body["timeline_count"] == 3
    timeline_ids = [ev["id"] for ev in body["timeline"]]
    assert timeline_ids == [str(e1.id), str(e2.id), str(e3.id)]
    assert body["timeline"][0]["timestamp"]
    assert body["timeline"][1]["event_type"] == "tool_result"
    assert body["timeline"][1]["tokens_in"] == 10

    # Same stored agent_events — no duplicate storage.
    stored = harness["events"].list_all_chronological(run.id)
    assert [str(e.id) for e in stored] == timeline_ids


def test_usage_and_cost_http(harness):
    client: TestClient = harness["client"]
    run = _seed_run(harness)
    harness["events"].add(
        run_id=run.id,
        agent_name="writer",
        agent_role=AgentRole.writer,
        event_type=AgentEventType.section_completed,
        payload={"model": "writer-model"},
        tokens_in=11,
        tokens_out=7,
        cost_usd=0.0004,
    )
    usage = client.get(f"/api/v1/runs/{run.id}/usage")
    assert usage.status_code == 200
    data = usage.json()["data"]
    assert data["total_tokens"] == 18
    assert data["total_cost_usd"] == pytest.approx(0.0004)

    cost = client.get(f"/api/v1/runs/{run.id}/cost")
    assert cost.status_code == 200
    assert cost.json()["data"]["cost_by_agent"]["writer"] == pytest.approx(0.0004)
