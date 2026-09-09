"""Phase 7 writer agent + report generation tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.writer_agent import WriterAgent, WrittenBlock, WrittenSection
from app.core.exceptions import NotFoundError
from app.main import app
from app.providers.openrouter import ChatCompletionResult
from app.schemas.agent_event import AgentEventResponse, AgentEventType, AgentRole
from app.schemas.finding import FindingResponse
from app.schemas.plan import SectionStatus
from app.schemas.report import ReportResponse
from app.schemas.run import CreateRunRequest, ResearchDepth, RunResponse, RunStatus
from app.schemas.section import SectionResponse
from app.schemas.source import SourceResponse
from app.services import agent_event_service as events_module
from app.services import report_service as report_module
from app.services import run_service as run_service_module
from app.services.agent_event_service import AgentEventService, _RunEventBus
from app.services.report_service import ReportService
from app.services.run_service import RunService
from app.api.v1 import agents_events as agents_events_api
from app.api.v1 import reports as reports_api


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


class FakeSectionsRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, SectionResponse] = {}

    def list_records_for_run(self, run_id: UUID) -> list[SectionResponse]:
        items = [r for r in self.rows.values() if r.run_id == run_id]
        return sorted(items, key=lambda s: s.order_index)

    def get_for_run(self, run_id: UUID, section_id: UUID) -> SectionResponse:
        row = self.rows.get(section_id)
        if row is None or row.run_id != run_id:
            raise NotFoundError("Section not found for this run")
        return row

    def update_fields(
        self, *, run_id: UUID, section_id: UUID, fields: dict[str, Any]
    ) -> SectionResponse:
        row = self.get_for_run(run_id, section_id)
        data = row.model_dump()
        data.update(fields)
        if "status" in fields and isinstance(fields["status"], str):
            data["status"] = SectionStatus(fields["status"])
        data["updated_at"] = datetime.now(timezone.utc)
        updated = SectionResponse(**data)
        self.rows[section_id] = updated
        return updated

    def add(
        self,
        *,
        run_id: UUID,
        title: str,
        sub_questions: list[str],
        order_index: int = 1,
    ) -> SectionResponse:
        section_id = uuid4()
        now = datetime.now(timezone.utc)
        record = SectionResponse(
            id=section_id,
            run_id=run_id,
            order_index=order_index,
            title=title,
            description=f"Desc {title}",
            sub_questions=sub_questions,
            status=SectionStatus.researched,
            created_at=now,
            updated_at=now,
        )
        self.rows[section_id] = record
        return record


class FakeFindingsRepository:
    def __init__(self) -> None:
        self.rows: list[FindingResponse] = []

    def list_for_run(self, run_id: UUID) -> list[FindingResponse]:
        return [r for r in self.rows if r.run_id == run_id]

    def list_for_section(self, run_id: UUID, section_id: UUID) -> list[FindingResponse]:
        return [r for r in self.list_for_run(run_id) if r.section_id == section_id]

    def add(self, **kwargs: Any) -> FindingResponse:
        finding = FindingResponse(
            id=kwargs.get("id") or uuid4(),
            run_id=kwargs["run_id"],
            section_id=kwargs["section_id"],
            claim=kwargs["claim"],
            source_id=kwargs.get("source_id"),
            confidence=float(kwargs.get("confidence") or 0.7),
            is_gap=bool(kwargs.get("is_gap") or False),
            created_at=datetime.now(timezone.utc),
        )
        self.rows.append(finding)
        return finding


class FakeSourcesRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, SourceResponse] = {}

    def list_for_run(self, run_id: UUID) -> list[SourceResponse]:
        return [r for r in self.rows.values() if r.run_id == run_id]

    def get_for_run(self, run_id: UUID, source_id: UUID) -> SourceResponse:
        row = self.rows.get(source_id)
        if row is None or row.run_id != run_id:
            raise NotFoundError("Source not found")
        return row

    def add(self, *, run_id: UUID, url: str, title: str) -> SourceResponse:
        source_id = uuid4()
        record = SourceResponse(
            id=source_id,
            run_id=run_id,
            url=url,
            domain="example.gov",
            title=title,
            published_at=None,
            quality_score=80.0,
            content_hash=str(uuid4()),
            extract_text="Evidence text " * 20,
            created_at=datetime.now(timezone.utc),
        )
        self.rows[source_id] = record
        return record


class FakeReportsRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, ReportResponse] = {}  # keyed by run_id

    def table_exists(self) -> bool:
        return True

    def get_for_run(self, run_id: UUID) -> ReportResponse:
        row = self.rows.get(run_id)
        if row is None:
            raise NotFoundError("Report not found for this run")
        return row

    def find_for_run(self, run_id: UUID) -> Optional[ReportResponse]:
        return self.rows.get(run_id)

    def upsert(self, row: dict[str, Any]) -> ReportResponse:
        run_id = UUID(str(row["run_id"]))
        now = datetime.now(timezone.utc)
        existing = self.rows.get(run_id)
        record = ReportResponse(
            id=UUID(str(row.get("id") or (existing.id if existing else uuid4()))),
            run_id=run_id,
            markdown=row.get("markdown") or "",
            sections_json=list(row.get("sections_json") or []),
            sections_confidence_json=row.get("sections_confidence_json"),
            unverified_claims_json=row.get("unverified_claims_json"),
            citation_map_json=dict(row.get("citation_map_json") or {}),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.rows[run_id] = record
        return record

    def update_for_run(self, run_id: UUID, fields: dict[str, Any]) -> ReportResponse:
        existing = self.get_for_run(run_id)
        data = existing.model_dump()
        data.update(fields)
        data["updated_at"] = datetime.now(timezone.utc)
        updated = ReportResponse(**data)
        self.rows[run_id] = updated
        return updated


class FakeAgentEventsRepository:
    def __init__(self) -> None:
        self.rows: list[AgentEventResponse] = []

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

    def get_by_id(self, run_id: UUID, event_id: UUID) -> AgentEventResponse:
        for row in self.rows:
            if row.run_id == run_id and row.id == event_id:
                return row
        raise NotFoundError("not found")

    def list_for_run(self, run_id: UUID, **kwargs: Any) -> tuple[list[AgentEventResponse], int]:
        items = [r for r in self.rows if r.run_id == run_id]
        return items, len(items)

    def list_all_chronological(self, run_id: UUID) -> list[AgentEventResponse]:
        return [r for r in self.rows if r.run_id == run_id]

    def count_for_run(self, run_id: UUID) -> int:
        return len([r for r in self.rows if r.run_id == run_id])


class FakeWriter(WriterAgent):
    def __init__(self) -> None:
        self.section_calls: list[dict[str, Any]] = []
        self.instructions_seen: list[str | None] = []

    def write_section(self, **kwargs: Any):  # type: ignore[override]
        self.section_calls.append(kwargs)
        self.instructions_seen.append(kwargs.get("instructions"))
        findings = kwargs.get("findings") or []
        sources = kwargs.get("sources") or []
        allowed = kwargs.get("allowed_citation_indices") or set()
        # Only cite allowed indices — and include a fabricated [99] to prove sanitize.
        cite = ""
        if allowed:
            cite = f" [{min(allowed)}]"
        body = (
            f"Discussion of {kwargs['section_title']}. "
            f"Evidence-based claims{cite}. Fabricated marker [99] should vanish."
        )
        if not findings or all(f.get("is_gap") for f in findings):
            body = f"Limited evidence for {kwargs['section_title']}."
        section = WrittenSection(
            section_id=kwargs["section_id"],
            title=kwargs["section_title"],
            markdown=WriterAgent._sanitize_citations(body, set(allowed)),
            confidence=0.2 if not findings else 0.8,
            citation_indices=sorted(set(allowed)),
            gap_notes=["No findings available"]
            if not findings or all(f.get("is_gap") for f in findings)
            else [],
            unverified_claims=[],
        )
        # Ensure only section findings were provided (caller responsibility asserted in tests).
        result = ChatCompletionResult(
            content="{}",
            requested_model="fake",
            model="fake",
            duration_ms=1,
            usage={"prompt_tokens": 10, "completion_tokens": 20},
        )
        return section, result

    def write_executive_summary(self, **kwargs: Any):  # type: ignore[override]
        return (
            WrittenBlock(markdown="Exec summary of the topic.", confidence=0.7),
            ChatCompletionResult(
                content="{}",
                requested_model="fake",
                model="fake",
                duration_ms=1,
                usage={"prompt_tokens": 5, "completion_tokens": 5},
            ),
        )

    def write_conclusion(self, **kwargs: Any):  # type: ignore[override]
        return (
            WrittenBlock(markdown="Conclusion with open questions.", confidence=0.7),
            ChatCompletionResult(
                content="{}",
                requested_model="fake",
                model="fake",
                duration_ms=1,
                usage={"prompt_tokens": 5, "completion_tokens": 5},
            ),
        )


@pytest.fixture
def harness():
    runs_repo = FakeRunsRepository()
    sections_repo = FakeSectionsRepository()
    findings_repo = FakeFindingsRepository()
    sources_repo = FakeSourcesRepository()
    reports_repo = FakeReportsRepository()
    events_repo = FakeAgentEventsRepository()

    runs = RunService(repository=runs_repo)
    events = AgentEventService(runs=runs, events=events_repo, bus=_RunEventBus())
    writer = FakeWriter()
    reports = ReportService(
        runs=runs,
        sections=sections_repo,
        findings=findings_repo,
        sources=sources_repo,
        reports=reports_repo,
        writer=writer,
        events=events,
    )

    original = {
        "run": run_service_module.run_service,
        "report": report_module.report_service,
        "events": events_module.agent_event_service,
        "api_reports": reports_api.report_service,
        "api_events": agents_events_api.agent_event_service,
    }
    run_service_module.run_service = runs
    report_module.report_service = reports
    events_module.agent_event_service = events
    reports_api.report_service = reports
    agents_events_api.agent_event_service = events

    client = TestClient(app)

    def seed(*, with_findings: bool = True, partial_gap: bool = False):
        run = runs.create_run(CreateRunRequest(topic="AI in clinical diagnostics"))
        runs.update_run_status(
            run.id, RunStatus.completed, skip_transition_check=True
        )
        run = runs.get_run(run.id)
        s1 = sections_repo.add(
            run_id=run.id,
            title="Clinical Evidence",
            sub_questions=["What evidence supports AI diagnostics?"],
            order_index=1,
        )
        s2 = sections_repo.add(
            run_id=run.id,
            title="Risks",
            sub_questions=["What risks remain?"],
            order_index=2,
        )
        src1 = sources_repo.add(
            run_id=run.id,
            url="https://www.nih.gov/ai-1",
            title="NIH Evidence",
        )
        src2 = sources_repo.add(
            run_id=run.id,
            url="https://www.nih.gov/ai-2",
            title="NIH Risks",
        )
        if with_findings:
            findings_repo.add(
                run_id=run.id,
                section_id=s1.id,
                claim="AI improves radiology screening accuracy in trials.",
                source_id=src1.id,
                confidence=0.8,
            )
            if partial_gap:
                findings_repo.add(
                    run_id=run.id,
                    section_id=s2.id,
                    claim="Research gap for risks sub-question",
                    is_gap=True,
                    confidence=0.0,
                )
            else:
                findings_repo.add(
                    run_id=run.id,
                    section_id=s2.id,
                    claim="Adoption risks include bias and workflow disruption.",
                    source_id=src2.id,
                    confidence=0.7,
                )
        return run, s1, s2, src1, src2

    yield {
        "client": client,
        "runs": runs,
        "sections": sections_repo,
        "findings": findings_repo,
        "sources": sources_repo,
        "reports_repo": reports_repo,
        "events_repo": events_repo,
        "reports": reports,
        "writer": writer,
        "seed": seed,
    }

    run_service_module.run_service = original["run"]
    report_module.report_service = original["report"]
    events_module.agent_event_service = original["events"]
    reports_api.report_service = original["api_reports"]
    agents_events_api.agent_event_service = original["api_events"]


def test_full_report_generation(harness):
    run, s1, s2, src1, src2 = harness["seed"]()
    result = harness["reports"].generate_report(run.id)
    assert result.sections_written == 2
    assert result.citations_used >= 1
    md = result.report.markdown
    assert md.startswith("# AI in clinical diagnostics")
    assert "## Executive Summary" in md
    assert "## Clinical Evidence" in md
    assert "## Risks" in md
    assert "## Conclusion" in md
    assert "## Sources" in md
    assert str(src1.id) in md
    assert "[99]" not in md  # fabricated citation stripped

    # Persistence
    loaded = harness["reports"].get_report(run.id)
    assert loaded.id == result.report.id
    assert loaded.markdown == md


def test_section_only_findings_passed(harness):
    run, s1, s2, *_ = harness["seed"]()
    harness["reports"].generate_report(run.id)
    writer: FakeWriter = harness["writer"]
    assert len(writer.section_calls) >= 2
    first = writer.section_calls[0]
    # First section should only receive findings for that section.
    claims = [f.get("claim", "") for f in first["findings"]]
    assert any("radiology" in c.lower() or "screening" in c.lower() for c in claims)
    assert not any("bias" in c.lower() for c in claims)


def test_section_generate_and_regenerate(harness):
    client: TestClient = harness["client"]
    run, s1, s2, *_ = harness["seed"]()

    gen = client.post(f"/api/v1/runs/{run.id}/sections/{s1.id}/generate")
    assert gen.status_code == 200, gen.text
    report = gen.json()["data"]
    assert "Clinical Evidence" in report["markdown"]
    assert len(report["sections_json"]) == 1

    regen = client.post(
        f"/api/v1/runs/{run.id}/sections/{s1.id}/regenerate",
        json={"instructions": "Make this section more technical"},
    )
    assert regen.status_code == 200, regen.text
    assert harness["writer"].instructions_seen[-1] == "Make this section more technical"
    # Still one section block (replaced, not duplicated)
    assert len(regen.json()["data"]["sections_json"]) == 1


def test_missing_findings_notes_gap(harness):
    run, s1, s2, *_ = harness["seed"](with_findings=False)
    result = harness["reports"].generate_report(run.id)
    assert result.gaps_noted >= 1
    assert "gap" in result.report.markdown.lower() or "Limited evidence" in result.report.markdown


def test_partial_research_with_gaps(harness):
    run, *_ = harness["seed"](partial_gap=True)
    runs: RunService = harness["runs"]
    runs.update_run_status(run.id, RunStatus.partial, skip_transition_check=True)
    result = harness["reports"].generate_report(run.id)
    assert result.sections_written == 2
    assert result.gaps_noted >= 1


def test_report_put_and_get_api(harness):
    client: TestClient = harness["client"]
    run, *_ = harness["seed"]()
    client.post(f"/api/v1/runs/{run.id}/report/generate")
    updated = client.put(
        f"/api/v1/runs/{run.id}/report",
        json={"markdown": "# Edited\n\nManual edit."},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["markdown"].startswith("# Edited")
    got = client.get(f"/api/v1/runs/{run.id}/report")
    assert got.status_code == 200
    assert got.json()["data"]["markdown"].startswith("# Edited")


def test_writer_events_emitted(harness):
    run, *_ = harness["seed"]()
    harness["reports"].generate_report(run.id)
    types = [e.event_type for e in harness["events_repo"].rows]
    assert AgentEventType.writer_started in types
    assert AgentEventType.section_started in types
    assert AgentEventType.section_completed in types
    assert AgentEventType.writer_finished in types


def test_citation_sanitize_unit():
    cleaned = WriterAgent._sanitize_citations("Claim [1] and fake [99].", {1})
    assert "[1]" in cleaned
    assert "[99]" not in cleaned
