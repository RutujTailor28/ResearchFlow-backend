"""Phase 5 multi-agent research + findings tests."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.researcher_agent import (
    MAX_TOOL_CALLS,
    ResearcherAgent,
    ResearcherDependencies,
    ResearcherOutcome,
)
from app.agents.research_graph import run_research_graph
from app.core.exceptions import NotFoundError
from app.main import app
from app.schemas.finding import FindingResponse
from app.schemas.plan import SectionStatus
from app.schemas.research_run import ResearchTaskResponse, ResearchTaskStatus
from app.schemas.run import ResearchDepth, RunResponse, RunStatus
from app.schemas.section import SectionResponse
from app.schemas.source import SourceResponse
from app.services import finding_service as finding_service_module
from app.services import research_orchestration_service as orch_module
from app.services import run_service as run_service_module
from app.services.agent_event_service import AgentEventService, _RunEventBus
from app.services.finding_service import FindingService
from app.services.research_orchestration_service import ResearchOrchestrationService
from app.services.run_service import RunService
from app.schemas.agent_event import AgentEventResponse, AgentEventType, AgentRole
from app.tools.extract import ExtractResult
from app.tools.fetch import FetchResult
from app.tools.quality import QualityBreakdown
from app.tools.search.base import SearchResponse, SearchResultItem


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
        raise NotFoundError("Agent event not found")

    def list_for_run(self, run_id: UUID, **kwargs: Any) -> tuple[list[AgentEventResponse], int]:
        items = [r for r in self.rows if r.run_id == run_id]
        return items, len(items)

    def list_all_chronological(self, run_id: UUID) -> list[AgentEventResponse]:
        return [r for r in self.rows if r.run_id == run_id]

    def count_for_run(self, run_id: UUID) -> int:
        return len([r for r in self.rows if r.run_id == run_id])


LONG_TEXT = (
    "Artificial intelligence in healthcare diagnostics improves screening accuracy "
    "across radiology and pathology workflows. Clinical trials report measurable "
    "gains in early detection rates when models assist clinicians. Adoption still "
    "depends on regulation, data quality, and integration with electronic health "
    "records used by hospitals worldwide. Evidence supports AI diagnostics for "
    "triage, prioritization, and second-reader support while clinical AI adoption "
    "also faces risks around bias, liability, and workflow disruption that teams "
    "must manage carefully during deployment and continuous monitoring programs."
)


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
            "plan_json": row.get("plan_json"),
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
        finished = row.get("finished_at")
        if isinstance(finished, str):
            finished = datetime.fromisoformat(finished.replace("Z", "+00:00"))
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
            finished_at=finished,
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
            description="",
            sub_questions=sub_questions,
            status=SectionStatus.pending,
            created_at=now,
            updated_at=now,
        )
        self.rows[section_id] = record
        return record


class FakeFindingsRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, FindingResponse] = {}

    def list_for_run(self, run_id: UUID) -> list[FindingResponse]:
        items = [r for r in self.rows.values() if r.run_id == run_id]
        return sorted(items, key=lambda f: f.created_at)

    def list_for_section(self, run_id: UUID, section_id: UUID) -> list[FindingResponse]:
        return [
            r
            for r in self.list_for_run(run_id)
            if r.section_id == section_id
        ]

    def get_for_run(self, run_id: UUID, finding_id: UUID) -> FindingResponse:
        row = self.rows.get(finding_id)
        if row is None or row.run_id != run_id:
            raise NotFoundError("Finding not found for this run")
        return row

    def create(self, row: dict[str, Any]) -> FindingResponse:
        finding_id = UUID(str(row["id"]))
        record = FindingResponse(
            id=finding_id,
            run_id=UUID(str(row["run_id"])),
            section_id=UUID(str(row["section_id"])),
            claim=row["claim"],
            source_id=UUID(str(row["source_id"])) if row.get("source_id") else None,
            confidence=float(row.get("confidence") or 0.5),
            is_gap=bool(row.get("is_gap") or False),
            created_at=datetime.now(timezone.utc),
        )
        self.rows[finding_id] = record
        return record

    def update(
        self, run_id: UUID, finding_id: UUID, fields: dict[str, Any]
    ) -> FindingResponse:
        row = self.get_for_run(run_id, finding_id)
        data = row.model_dump()
        data.update(fields)
        if "source_id" in fields and fields["source_id"] is not None:
            data["source_id"] = UUID(str(fields["source_id"]))
        updated = FindingResponse(**data)
        self.rows[finding_id] = updated
        return updated

    def delete(self, run_id: UUID, finding_id: UUID) -> None:
        self.get_for_run(run_id, finding_id)
        del self.rows[finding_id]


class FakeTasksRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, ResearchTaskResponse] = {}

    def list_for_run(self, run_id: UUID) -> list[ResearchTaskResponse]:
        items = [r for r in self.rows.values() if r.run_id == run_id]
        return sorted(items, key=lambda t: t.created_at)

    def list_failed(self, run_id: UUID) -> list[ResearchTaskResponse]:
        return [t for t in self.list_for_run(run_id) if t.status == ResearchTaskStatus.failed]

    def create_many(self, rows: list[dict[str, Any]]) -> list[ResearchTaskResponse]:
        out: list[ResearchTaskResponse] = []
        for row in rows:
            task_id = UUID(str(row["id"]))
            record = ResearchTaskResponse(
                id=task_id,
                run_id=UUID(str(row["run_id"])),
                section_id=UUID(str(row["section_id"])),
                sub_question=row["sub_question"],
                status=ResearchTaskStatus(row.get("status") or "pending"),
                tool_calls=int(row.get("tool_calls") or 0),
                error=row.get("error"),
                gap_note=row.get("gap_note"),
                created_at=datetime.now(timezone.utc),
                finished_at=None,
            )
            self.rows[task_id] = record
            out.append(record)
        return out

    def update(self, task_id: UUID, fields: dict[str, Any]) -> ResearchTaskResponse:
        row = self.rows[task_id]
        data = row.model_dump()
        data.update(fields)
        if "status" in fields:
            data["status"] = ResearchTaskStatus(fields["status"])
        if isinstance(data.get("finished_at"), str):
            data["finished_at"] = datetime.fromisoformat(
                data["finished_at"].replace("Z", "+00:00")
            )
        updated = ResearchTaskResponse(**data)
        self.rows[task_id] = updated
        return updated

    def delete_for_run(self, run_id: UUID) -> None:
        for task_id in [tid for tid, r in self.rows.items() if r.run_id == run_id]:
            del self.rows[task_id]

    def mark_running(self, task_id: UUID) -> ResearchTaskResponse:
        return self.update(task_id, {"status": ResearchTaskStatus.running.value})

    def mark_completed(self, task_id: UUID, *, tool_calls: int) -> ResearchTaskResponse:
        return self.update(
            task_id,
            {
                "status": ResearchTaskStatus.completed.value,
                "tool_calls": tool_calls,
                "error": None,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def mark_failed(
        self,
        task_id: UUID,
        *,
        tool_calls: int,
        error: str,
        gap_note: str | None = None,
    ) -> ResearchTaskResponse:
        return self.update(
            task_id,
            {
                "status": ResearchTaskStatus.failed.value,
                "tool_calls": tool_calls,
                "error": error,
                "gap_note": gap_note or error,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )


class FakeSourcesRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, SourceResponse] = {}

    def find_by_url(self, run_id: UUID, url: str) -> Optional[SourceResponse]:
        for row in self.rows.values():
            if row.run_id == run_id and row.url == url:
                return row
        return None

    def find_by_content_hash(
        self, run_id: UUID, content_hash: str
    ) -> Optional[SourceResponse]:
        for row in self.rows.values():
            if row.run_id == run_id and row.content_hash == content_hash:
                return row
        return None

    def get_for_run(self, run_id: UUID, source_id: UUID) -> SourceResponse:
        row = self.rows.get(source_id)
        if row is None or row.run_id != run_id:
            raise NotFoundError("Source not found for this run")
        return row

    def create(self, row: dict[str, Any]) -> SourceResponse:
        source_id = UUID(str(row["id"]))
        record = SourceResponse(
            id=source_id,
            run_id=UUID(str(row["run_id"])),
            url=row["url"],
            domain=row["domain"],
            title=row["title"],
            published_at=None,
            quality_score=float(row["quality_score"]),
            content_hash=row["content_hash"],
            extract_text=row["extract_text"],
            created_at=datetime.now(timezone.utc),
        )
        self.rows[source_id] = record
        return record


def _success_deps() -> ResearcherDependencies:
    def search(query: str, max_results: int = 5) -> SearchResponse:
        return SearchResponse(
            query=query,
            provider="fake",
            results=[
                SearchResultItem(
                    title="NIH AI diagnostics",
                    url="https://www.nih.gov/ai-diagnostics",
                    snippet="AI diagnostics healthcare screening pathology radiology clinical",
                    source="fake",
                )
            ],
        )

    def fetch(url: str) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            html=f"<html><body><p>{LONG_TEXT}</p></body></html>",
            headers={},
        )

    def extract(html: str, fallback_title: str = "") -> ExtractResult:
        return ExtractResult(title=fallback_title or "NIH", text=LONG_TEXT, word_count=60)

    def score(**kwargs: Any) -> QualityBreakdown:
        return QualityBreakdown(score=80.0, reasons=["gov"])

    return ResearcherDependencies(search=search, fetch=fetch, extract=extract, score=score)


def _failing_deps() -> ResearcherDependencies:
    def search(query: str, max_results: int = 5) -> SearchResponse:
        raise RuntimeError("search boom")

    return ResearcherDependencies(
        search=search,
        fetch=lambda url: None,
        extract=lambda html, fallback_title="": None,
        score=lambda **kwargs: QualityBreakdown(score=0, reasons=[]),
    )


@pytest.fixture
def harness():
    runs_repo = FakeRunsRepository()
    sections_repo = FakeSectionsRepository()
    findings_repo = FakeFindingsRepository()
    tasks_repo = FakeTasksRepository()
    sources_repo = FakeSourcesRepository()

    runs = RunService(repository=runs_repo)
    events = AgentEventService(
        runs=runs,
        events=FakeAgentEventsRepository(),
        bus=_RunEventBus(),
    )
    researcher = ResearcherAgent(deps=_success_deps(), events=events)
    orch = ResearchOrchestrationService(
        runs=runs,
        sections=sections_repo,
        tasks=tasks_repo,
        findings=findings_repo,
        sources=sources_repo,
        researcher=researcher,
        events=events,
    )
    findings = FindingService(
        runs=runs,
        findings=findings_repo,
        sections=sections_repo,
        sources=sources_repo,
    )

    # Wire into FastAPI dependency singletons used by routers.
    original = {
        "run": run_service_module.run_service,
        "orch": orch_module.research_orchestration_service,
        "find": finding_service_module.finding_service,
    }
    run_service_module.run_service = runs
    orch_module.research_orchestration_service = orch
    finding_service_module.finding_service = findings

    # Routers import the service objects at module load — rebind API modules too.
    from app.api.v1 import findings as findings_api
    from app.api.v1 import run_research as run_research_api

    findings_api.finding_service = findings
    run_research_api.research_orchestration_service = orch

    client = TestClient(app)

    def seed_approved(
        *,
        questions: list[tuple[str, list[str]]] | None = None,
    ) -> tuple[RunResponse, list[SectionResponse]]:
        run = runs.create_run(
            __import__("app.schemas.run", fromlist=["CreateRunRequest"]).CreateRunRequest(
                topic="AI in healthcare diagnostics"
            )
        )
        runs.update_run_status(run.id, RunStatus.plan_ready)
        runs.update_run_status(run.id, RunStatus.approved)
        run = runs.get_run(run.id)
        qs = questions or [
            ("Clinical use", ["What evidence supports AI diagnostics in radiology?"]),
            ("Risks", ["What risks remain for clinical AI adoption?"]),
        ]
        secs: list[SectionResponse] = []
        for i, (title, subs) in enumerate(qs, start=1):
            secs.append(
                sections_repo.add(
                    run_id=run.id, title=title, sub_questions=subs, order_index=i
                )
            )
        return run, secs

    yield {
        "client": client,
        "runs": runs,
        "sections": sections_repo,
        "findings_repo": findings_repo,
        "tasks": tasks_repo,
        "sources": sources_repo,
        "orch": orch,
        "seed": seed_approved,
    }

    run_service_module.run_service = original["run"]
    orch_module.research_orchestration_service = original["orch"]
    finding_service_module.finding_service = original["find"]
    findings_api.finding_service = original["find"]
    run_research_api.research_orchestration_service = original["orch"]


def test_max_tool_calls_enforced():
    calls = {"n": 0}

    def search(query: str, max_results: int = 5) -> SearchResponse:
        calls["n"] += 1
        return SearchResponse(query=query, provider="fake", results=[])

    agent = ResearcherAgent(
        deps=ResearcherDependencies(
            search=search,
            fetch=lambda url: None,
            extract=lambda html, fallback_title="": None,
            score=lambda **kwargs: QualityBreakdown(score=0, reasons=[]),
        )
    )
    outcome = agent.research(
        task_id=uuid4(),
        section_id=uuid4(),
        sub_question="What is missing evidence here?",
        topic="Topic",
    )
    assert outcome.success is False
    assert outcome.tool_calls <= MAX_TOOL_CALLS
    assert calls["n"] <= MAX_TOOL_CALLS
    assert outcome.gap_note


def test_sub_question_assignment_and_parallel(harness):
    started: list[str] = []
    barrier = {"count": 0}

    def search(query: str, max_results: int = 5) -> SearchResponse:
        started.append(query)
        barrier["count"] += 1
        # Hold briefly so overlapping workers are observable.
        time.sleep(0.05)
        return SearchResponse(
            query=query,
            provider="fake",
            results=[
                SearchResultItem(
                    title="t",
                    url=f"https://www.nih.gov/{len(started)}",
                    snippet="AI diagnostics healthcare screening pathology radiology clinical",
                    source="fake",
                )
            ],
        )

    deps = ResearcherDependencies(
        search=search,
        fetch=_success_deps().fetch,
        extract=_success_deps().extract,
        score=_success_deps().score,
    )
    agent = ResearcherAgent(deps=deps)
    harness["orch"]._researcher = agent

    run, sections = harness["seed"]()
    questions = [q for s in sections for q in s.sub_questions]
    assert len(questions) == 2

    result = harness["orch"].start_research(run.id, sync=True)
    assert result.task_count == 2

    tasks = harness["tasks"].list_for_run(run.id)
    assert {t.sub_question for t in tasks} == set(questions)
    assert all(t.status == ResearchTaskStatus.completed for t in tasks)

    findings = harness["findings_repo"].list_for_run(run.id)
    assert len(findings) == 2
    assert all(not f.is_gap for f in findings)
    assert all(f.source_id is not None for f in findings)
    assert len(started) >= 2


def test_failed_researcher_does_not_stop_run(harness):
    class MixedAgent(ResearcherAgent):
        def research(self, **kwargs):  # type: ignore[no-untyped-def]
            if "risks" in kwargs["sub_question"].lower():
                return ResearcherOutcome(
                    task_id=kwargs["task_id"],
                    section_id=kwargs["section_id"],
                    sub_question=kwargs["sub_question"],
                    success=False,
                    tool_calls=1,
                    gap_note="Missing research gap for risks",
                    error="forced failure",
                )
            return ResearcherAgent.research(self, **kwargs)

    harness["orch"]._researcher = MixedAgent(deps=_success_deps())
    run, _ = harness["seed"]()
    harness["orch"].start_research(run.id, sync=True)

    status = harness["orch"].get_status(run.id)
    assert status.completed == 1
    assert status.failed == 1
    assert status.run_status == RunStatus.partial.value

    results = harness["orch"].get_results(run.id)
    assert results.findings_count == 2
    assert results.gaps_count == 1


def test_retry_research(harness):
    class FailOnce(ResearcherAgent):
        def __init__(self) -> None:
            super().__init__(deps=_success_deps())
            self.fail_next = True

        def research(self, **kwargs):  # type: ignore[no-untyped-def]
            if self.fail_next and "risks" in kwargs["sub_question"].lower():
                self.fail_next = False
                return ResearcherOutcome(
                    task_id=kwargs["task_id"],
                    section_id=kwargs["section_id"],
                    sub_question=kwargs["sub_question"],
                    success=False,
                    tool_calls=2,
                    gap_note="temporary gap",
                    error="temp",
                )
            return ResearcherAgent.research(self, **kwargs)

    agent = FailOnce()
    harness["orch"]._researcher = agent
    run, _ = harness["seed"]()
    harness["orch"].start_research(run.id, sync=True)
    assert harness["orch"].get_status(run.id).failed == 1

    retry = harness["orch"].retry_research(run.id, sync=True)
    assert retry.task_count == 1
    status = harness["orch"].get_status(run.id)
    assert status.failed == 0
    assert status.completed == 2
    assert status.run_status == RunStatus.completed.value


def test_research_api_and_findings_crud(harness):
    client: TestClient = harness["client"]
    run, sections = harness["seed"]()

    start = client.post(f"/api/v1/runs/{run.id}/research/start?sync=true")
    assert start.status_code == 200
    assert start.json()["data"]["task_count"] == 2

    status = client.get(f"/api/v1/runs/{run.id}/research/status")
    assert status.status_code == 200
    body = status.json()["data"]
    assert body["completed"] == 2

    results = client.get(f"/api/v1/runs/{run.id}/research/results")
    assert results.status_code == 200
    assert results.json()["data"]["findings_count"] == 2

    listed = client.get(f"/api/v1/runs/{run.id}/findings")
    assert listed.status_code == 200
    items = listed.json()["data"]["items"]
    assert len(items) == 2
    finding_id = items[0]["id"]

    section_id = str(sections[0].id)
    by_section = client.get(f"/api/v1/runs/{run.id}/sections/{section_id}/findings")
    assert by_section.status_code == 200
    assert by_section.json()["data"]["total"] >= 1

    got = client.get(f"/api/v1/runs/{run.id}/findings/{finding_id}")
    assert got.status_code == 200

    updated = client.put(
        f"/api/v1/runs/{run.id}/findings/{finding_id}",
        json={"claim": "Updated paraphrased claim for testing.", "confidence": 0.7},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["claim"].startswith("Updated")

    created = client.post(
        f"/api/v1/runs/{run.id}/findings",
        json={
            "section_id": section_id,
            "claim": "Manual gap recorded by user after review.",
            "confidence": 0.0,
            "is_gap": True,
        },
    )
    assert created.status_code == 200
    manual_id = created.json()["data"]["id"]

    deleted = client.delete(f"/api/v1/runs/{run.id}/findings/{manual_id}")
    assert deleted.status_code == 200


def test_start_requires_approved(harness):
    client: TestClient = harness["client"]
    run = harness["runs"].create_run(
        __import__("app.schemas.run", fromlist=["CreateRunRequest"]).CreateRunRequest(
            topic="Not approved yet topic"
        )
    )
    response = client.post(f"/api/v1/runs/{run.id}/research/start?sync=true")
    assert response.status_code == 409


def test_graph_collects_all_outcomes():
    agent = ResearcherAgent(deps=_success_deps())
    t1, t2 = uuid4(), uuid4()
    s1, s2 = uuid4(), uuid4()
    outcomes = run_research_graph(
        run_id=uuid4(),
        topic="Topic",
        tasks=[
            {
                "id": str(t1),
                "section_id": str(s1),
                "sub_question": "What evidence supports AI diagnostics in radiology?",
            },
            {
                "id": str(t2),
                "section_id": str(s2),
                "sub_question": "What risks remain for clinical AI adoption?",
            },
        ],
        researcher=agent,
        max_workers=2,
    )
    assert len(outcomes) == 2
    assert all(o.success for o in outcomes)
    assert all(o.tool_calls <= MAX_TOOL_CALLS for o in outcomes)
