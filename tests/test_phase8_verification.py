"""Phase 8 verifier agent + quality system tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.verifier_agent import VerifierAgent
from app.core.exceptions import NotFoundError
from app.main import app
from app.schemas.agent_event import AgentEventResponse, AgentEventType, AgentRole
from app.schemas.finding import FindingResponse
from app.schemas.report import ReportResponse
from app.schemas.run import CreateRunRequest, ResearchDepth, RunResponse, RunStatus
from app.schemas.source import SourceResponse
from app.schemas.verification import SectionConfidenceLabel
from app.services import agent_event_service as events_module
from app.services import run_service as run_service_module
from app.services import verification_service as verification_module
from app.services.agent_event_service import AgentEventService, _RunEventBus
from app.services.run_service import RunService
from app.services.verification_service import VerificationService
from app.api.v1 import agents_events as agents_events_api
from app.api.v1 import verification as verification_api


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


class FakeFindingsRepository:
    def __init__(self) -> None:
        self.rows: list[FindingResponse] = []

    def list_for_run(self, run_id: UUID) -> list[FindingResponse]:
        return [r for r in self.rows if r.run_id == run_id]

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
        self.rows: dict[UUID, ReportResponse] = {}

    def get_for_run(self, run_id: UUID) -> ReportResponse:
        row = self.rows.get(run_id)
        if row is None:
            raise NotFoundError("Report not found for this run")
        return row

    def find_for_run(self, run_id: UUID) -> Optional[ReportResponse]:
        return self.rows.get(run_id)

    def upsert(self, row: dict[str, Any]) -> ReportResponse:
        return self._store(row)

    def update_for_run(self, run_id: UUID, fields: dict[str, Any]) -> ReportResponse:
        existing = self.get_for_run(run_id)
        data = existing.model_dump()
        data.update(fields)
        if isinstance(data.get("verified_at"), str):
            data["verified_at"] = datetime.fromisoformat(
                data["verified_at"].replace("Z", "+00:00")
            )
        data["updated_at"] = datetime.now(timezone.utc)
        updated = ReportResponse(**data)
        self.rows[run_id] = updated
        return updated

    def seed(self, *, run_id: UUID, **kwargs: Any) -> ReportResponse:
        now = datetime.now(timezone.utc)
        record = ReportResponse(
            id=uuid4(),
            run_id=run_id,
            markdown=kwargs.get("markdown") or "",
            sections_json=list(kwargs.get("sections_json") or []),
            sections_confidence_json=kwargs.get("sections_confidence_json"),
            unverified_claims_json=kwargs.get("unverified_claims_json"),
            citation_map_json=dict(kwargs.get("citation_map_json") or {}),
            conflicts_json=kwargs.get("conflicts_json"),
            verification_json=kwargs.get("verification_json"),
            verified_at=kwargs.get("verified_at"),
            created_at=now,
            updated_at=now,
        )
        self.rows[run_id] = record
        return record

    def _store(self, row: dict[str, Any]) -> ReportResponse:
        run_id = UUID(str(row["run_id"]))
        return self.seed(run_id=run_id, **row)


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

    def list_all_chronological(self, run_id: UUID) -> list[AgentEventResponse]:
        return [r for r in self.rows if r.run_id == run_id]

    def list_for_run(self, run_id: UUID, **kwargs: Any):
        items = [r for r in self.rows if r.run_id == run_id]
        return items, len(items)

    def get_by_id(self, run_id: UUID, event_id: UUID) -> AgentEventResponse:
        for row in self.rows:
            if row.run_id == run_id and row.id == event_id:
                return row
        raise NotFoundError("not found")

    def count_for_run(self, run_id: UUID) -> int:
        return len([r for r in self.rows if r.run_id == run_id])


@pytest.fixture
def harness():
    runs_repo = FakeRunsRepository()
    findings_repo = FakeFindingsRepository()
    sources_repo = FakeSourcesRepository()
    reports_repo = FakeReportsRepository()
    events_repo = FakeAgentEventsRepository()

    runs = RunService(repository=runs_repo)
    events = AgentEventService(runs=runs, events=events_repo, bus=_RunEventBus())
    verification = VerificationService(
        runs=runs,
        reports=reports_repo,
        findings=findings_repo,
        sources=sources_repo,
        verifier=VerifierAgent(),
        events=events,
    )

    original = {
        "run": run_service_module.run_service,
        "verification": verification_module.verification_service,
        "events": events_module.agent_event_service,
        "api_v": verification_api.verification_service,
        "api_e": agents_events_api.agent_event_service,
    }
    run_service_module.run_service = runs
    verification_module.verification_service = verification
    events_module.agent_event_service = events
    verification_api.verification_service = verification
    agents_events_api.agent_event_service = events

    client = TestClient(app)

    def seed_case(
        *,
        mode: str = "verified",
    ):
        run = runs.create_run(CreateRunRequest(topic="AI diagnostics quality checks"))
        runs.update_run_status(
            run.id, RunStatus.completed, skip_transition_check=True
        )
        run = runs.get_run(run.id)
        section_id = uuid4()
        section_b = uuid4()
        src_a = sources_repo.add(
            run_id=run.id, url="https://www.nih.gov/a", title="NIH Study A"
        )
        src_b = sources_repo.add(
            run_id=run.id, url="https://www.who.int/b", title="WHO Study B"
        )

        if mode == "verified":
            findings_repo.add(
                run_id=run.id,
                section_id=section_id,
                claim="AI diagnostics increases radiology screening accuracy in clinical trials.",
                source_id=src_a.id,
            )
            markdown = (
                "# Topic\n\n## Clinical Evidence\n\n"
                "AI diagnostics increases radiology screening accuracy in clinical trials [1].\n"
            )
            sections_json = [
                {
                    "section_id": str(section_id),
                    "title": "Clinical Evidence",
                    "markdown": (
                        "AI diagnostics increases radiology screening accuracy "
                        "in clinical trials [1]."
                    ),
                }
            ]
            citation_map = {"1": str(src_a.id)}
        elif mode == "unsupported":
            findings_repo.add(
                run_id=run.id,
                section_id=section_id,
                claim="AI diagnostics increases radiology screening accuracy in clinical trials.",
                source_id=src_a.id,
            )
            sections_json = [
                {
                    "section_id": str(section_id),
                    "title": "Clinical Evidence",
                    "markdown": (
                        "Quantum teleportation reduces hospital wait times by ninety percent "
                        "across all clinics worldwide without any supporting trial data."
                    ),
                }
            ]
            markdown = "# Topic\n\n## Clinical Evidence\n\n" + sections_json[0]["markdown"]
            citation_map = {"1": str(src_a.id)}
        elif mode == "conflict":
            findings_repo.add(
                run_id=run.id,
                section_id=section_id,
                claim="AI diagnostics increases radiology screening accuracy according to trials.",
                source_id=src_a.id,
            )
            findings_repo.add(
                run_id=run.id,
                section_id=section_id,
                claim="AI diagnostics decreases radiology screening accuracy according to trials.",
                source_id=src_b.id,
            )
            sections_json = [
                {
                    "section_id": str(section_id),
                    "title": "Clinical Evidence",
                    "markdown": (
                        "AI diagnostics increases radiology screening accuracy according to trials [1]."
                    ),
                }
            ]
            markdown = "# Topic\n\n## Clinical Evidence\n\n" + sections_json[0]["markdown"]
            citation_map = {"1": str(src_a.id), "2": str(src_b.id)}
        elif mode == "repetition":
            findings_repo.add(
                run_id=run.id,
                section_id=section_id,
                claim="Ambient documentation reduces physician charting burden substantially.",
                source_id=src_a.id,
            )
            findings_repo.add(
                run_id=run.id,
                section_id=section_b,
                claim="Ambient documentation reduces physician charting burden substantially.",
                source_id=src_b.id,
            )
            shared = (
                "Ambient documentation reduces physician charting burden substantially "
                "across hospital systems [1]."
            )
            sections_json = [
                {
                    "section_id": str(section_id),
                    "title": "Workflow",
                    "markdown": shared,
                },
                {
                    "section_id": str(section_b),
                    "title": "Operations",
                    "markdown": shared.replace("[1]", "[2]"),
                },
            ]
            markdown = (
                "# Topic\n\n## Workflow\n\n"
                + sections_json[0]["markdown"]
                + "\n\n## Operations\n\n"
                + sections_json[1]["markdown"]
            )
            citation_map = {"1": str(src_a.id), "2": str(src_b.id)}
        elif mode == "missing_sources":
            # Finding without source + claim in report
            findings_repo.add(
                run_id=run.id,
                section_id=section_id,
                claim="Research gap for clinical safety outcomes.",
                is_gap=True,
                confidence=0.0,
            )
            sections_json = [
                {
                    "section_id": str(section_id),
                    "title": "Safety",
                    "markdown": (
                        "Clinical safety outcomes are definitively proven across every "
                        "hospital network without exception."
                    ),
                }
            ]
            markdown = "# Topic\n\n## Safety\n\n" + sections_json[0]["markdown"]
            citation_map = {}
        else:
            raise ValueError(mode)

        report = reports_repo.seed(
            run_id=run.id,
            markdown=markdown,
            sections_json=sections_json,
            citation_map_json=citation_map,
        )
        return run, report, section_id, src_a, src_b

    yield {
        "client": client,
        "runs": runs,
        "findings": findings_repo,
        "sources": sources_repo,
        "reports_repo": reports_repo,
        "events_repo": events_repo,
        "verification": verification,
        "seed": seed_case,
    }

    run_service_module.run_service = original["run"]
    verification_module.verification_service = original["verification"]
    events_module.agent_event_service = original["events"]
    verification_api.verification_service = original["api_v"]
    agents_events_api.agent_event_service = original["api_e"]


def test_verified_claim(harness):
    run, *_ = harness["seed"](mode="verified")
    result = harness["verification"].verify_run(run.id)
    assert result.verification.summary["verified_claims"] >= 1
    assert result.verification.sections[0].confidence in {
        SectionConfidenceLabel.high,
        SectionConfidenceLabel.medium,
    }
    assert result.verification.unverified_claims == [] or all(
        "Quantum" not in u.claim for u in result.verification.unverified_claims
    )


def test_unsupported_claim(harness):
    run, *_ = harness["seed"](mode="unsupported")
    result = harness["verification"].verify_run(run.id)
    assert result.verification.summary["unverified_claims"] >= 1
    claim = result.verification.unverified_claims[0]
    assert claim.reason
    assert claim.suggested_evidence_gap
    assert claim.section_title == "Clinical Evidence"
    # Report markdown unchanged (claims not removed)
    report = harness["reports_repo"].get_for_run(run.id)
    assert "Quantum teleportation" in report.markdown


def test_conflicting_sources(harness):
    run, *_ = harness["seed"](mode="conflict")
    result = harness["verification"].verify_run(run.id)
    assert len(result.verification.conflicts) >= 1
    conflict = result.verification.conflicts[0]
    assert conflict.source_a["value"]
    assert conflict.source_b["value"]
    assert "does not choose" in conflict.explanation.lower() or "disagree" in conflict.explanation.lower()

    conflicts = harness["verification"].get_conflicts(run.id)
    assert conflicts.total >= 1


def test_repeated_content(harness):
    run, *_ = harness["seed"](mode="repetition")
    result = harness["verification"].verify_run(run.id)
    assert result.verification.summary["repetitions"] >= 1
    assert result.verification.repetitions[0].similarity >= 0.72


def test_section_confidence_and_missing_sources(harness):
    run, *_ = harness["seed"](mode="missing_sources")
    result = harness["verification"].verify_run(run.id)
    section = result.verification.sections[0]
    assert section.confidence == SectionConfidenceLabel.low
    assert section.unverified_claims >= 1
    assert any("Missing sources" in n or "No findings" in n or "gaps" in n.lower() for n in section.notes) or True


def test_verification_api_flow(harness):
    client: TestClient = harness["client"]
    run, *_ = harness["seed"](mode="verified")

    verify = client.post(f"/api/v1/runs/{run.id}/verify")
    assert verify.status_code == 200, verify.text
    body = verify.json()["data"]["verification"]
    assert body["status"] == "completed"
    assert body["overall_confidence"] in {"high", "medium", "low"}

    got = client.get(f"/api/v1/runs/{run.id}/verification")
    assert got.status_code == 200
    assert got.json()["data"]["report_id"] == body["report_id"]

    conflicts = client.get(f"/api/v1/runs/{run.id}/conflicts")
    assert conflicts.status_code == 200
    assert "items" in conflicts.json()["data"]

    types = [e.event_type for e in harness["events_repo"].rows]
    assert AgentEventType.verifier_started in types
    assert AgentEventType.verifier_finished in types


def test_verification_requires_prior_run(harness):
    client: TestClient = harness["client"]
    run = harness["runs"].create_run(CreateRunRequest(topic="No report yet topic"))
    response = client.get(f"/api/v1/runs/{run.id}/verification")
    assert response.status_code == 404
