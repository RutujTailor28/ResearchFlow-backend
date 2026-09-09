"""Phase 2 planner API tests (OpenRouter mocked)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.planner_agent import PlannerAgent
from app.core.exceptions import AiParseError
from app.main import app
from app.providers.openrouter import ChatCompletionResult
from app.schemas.plan import PlanSection, ResearchPlan, SectionStatus
from app.schemas.run import ResearchDepth, RunResponse, RunStatus
from app.services import planner_service as planner_module
from app.services import run_service as run_service_module
from app.services.planner_service import PlannerService
from app.services.run_service import RunService


def _sample_ai_json(*, sections: int = 5) -> str:
    section_list = []
    for i in range(1, sections + 1):
        section_list.append(
            {
                "id": f"section-{i}",
                "title": f"Section {i} Title",
                "description": f"Description for section {i}",
                "sub_questions": [f"Question {i}.1?", f"Question {i}.2?"],
            }
        )
    import json

    return json.dumps(
        {
            "title": "AI Healthcare Research Plan",
            "summary": "A structured plan for researching AI in healthcare.",
            "sections": section_list,
        }
    )


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
        }
        self.rows[run_id] = created
        return self._to_response(created)

    def get_by_id(self, run_id: UUID) -> Optional[RunResponse]:
        row = self.rows.get(run_id)
        return self._to_response(row) if row else None

    def list(self, **kwargs: Any) -> tuple[list[RunResponse], int]:
        items = [self._to_response(r) for r in self.rows.values()]
        return items, len(items)

    def update(self, run_id: UUID, fields: dict[str, Any]) -> RunResponse:
        from app.core.exceptions import NotFoundError

        if run_id not in self.rows:
            raise NotFoundError("Run not found")
        self.rows[run_id] = {**self.rows[run_id], **fields}
        return self._to_response(self.rows[run_id])

    def delete(self, run_id: UUID) -> None:
        from app.core.exceptions import NotFoundError

        if run_id not in self.rows:
            raise NotFoundError("Run not found")
        del self.rows[run_id]

    def table_exists(self) -> bool:
        return True

    @staticmethod
    def _to_response(row: dict[str, Any]) -> RunResponse:
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


class FakePlanSectionsRepository:
    def __init__(self) -> None:
        self.by_run: dict[UUID, list[PlanSection]] = {}

    def table_exists(self) -> bool:
        return True

    def list_for_run(self, run_id: UUID) -> list[PlanSection]:
        return list(self.by_run.get(run_id, []))

    def replace_for_run(self, run_id: UUID, plan: ResearchPlan) -> list[PlanSection]:
        sections = []
        for index, section in enumerate(plan.sections, start=1):
            sections.append(
                PlanSection(
                    id=f"section-{index}",
                    order_index=index,
                    title=section.title,
                    description=section.description,
                    sub_questions=section.sub_questions,
                    status=SectionStatus.pending,
                )
            )
        self.by_run[run_id] = sections
        return sections

    def delete_for_run(self, run_id: UUID) -> None:
        self.by_run.pop(run_id, None)


class FakeOpenRouter:
    def __init__(self, content: str | None = None, fail: bool = False) -> None:
        self.content = content or _sample_ai_json()
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> ChatCompletionResult:
        self.calls.append(kwargs)
        if self.fail:
            raise AiParseError("forced failure")
        return ChatCompletionResult(
            content=self.content,
            requested_model="test-model",
            model="test-model",
            duration_ms=42,
        )


@pytest.fixture
def fake_runs() -> FakeRunsRepository:
    return FakeRunsRepository()


@pytest.fixture
def fake_sections() -> FakePlanSectionsRepository:
    return FakePlanSectionsRepository()


@pytest.fixture
def fake_ai() -> FakeOpenRouter:
    return FakeOpenRouter()


@pytest.fixture
def client(
    fake_runs: FakeRunsRepository,
    fake_sections: FakePlanSectionsRepository,
    fake_ai: FakeOpenRouter,
):
    runs = RunService(repository=fake_runs)
    agent = PlannerAgent(client=fake_ai)  # type: ignore[arg-type]

    class _SilentEvents:
        def emit(self, **kwargs):  # noqa: ANN003
            from app.schemas.agent_event import AgentEventResponse, AgentEventType, AgentRole
            from datetime import datetime, timezone
            from uuid import uuid4

            return AgentEventResponse(
                id=uuid4(),
                run_id=kwargs["run_id"],
                agent_name=kwargs["agent_name"],
                agent_role=AgentRole(kwargs["agent_role"]),
                event_type=AgentEventType(kwargs["event_type"]),
                payload_json=kwargs.get("payload") or {},
                tokens_in=kwargs.get("tokens_in") or 0,
                tokens_out=kwargs.get("tokens_out") or 0,
                cost_usd=kwargs.get("cost_usd") or 0,
                created_at=datetime.now(timezone.utc),
            )

    planner = PlannerService(
        runs=runs, sections=fake_sections, agent=agent, events=_SilentEvents()  # type: ignore[arg-type]
    )

    original_run = run_service_module.run_service
    original_planner = planner_module.planner_service
    run_service_module.run_service = runs
    planner_module.planner_service = planner

    # Rebind API module references (they imported symbols at load time)
    from app.api.v1 import plans as plans_api
    from app.api.v1 import runs as runs_api

    original_runs_svc = runs_api.run_service
    original_plans_svc = plans_api.planner_service
    runs_api.run_service = runs
    plans_api.planner_service = planner

    with TestClient(app) as test_client:
        yield test_client

    run_service_module.run_service = original_run
    planner_module.planner_service = original_planner
    runs_api.run_service = original_runs_svc
    plans_api.planner_service = original_plans_svc


def _create_run(client: TestClient, depth: str = "standard") -> dict:
    response = client.post(
        "/api/v1/runs",
        json={
            "topic": "Future of Artificial Intelligence in Healthcare",
            "depth": depth,
            "audience": "technical",
            "tone": "professional",
            "target_length": 3000,
            "source_types": ["official", "research", "news"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_generate_ai_plan(client: TestClient, fake_ai: FakeOpenRouter) -> None:
    run = _create_run(client)
    response = client.post(f"/api/v1/runs/{run['id']}/plan/generate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"]["run_status"] == "plan_ready"
    assert body["data"]["meta"]["generation_mode"] == "ai"
    assert len(body["data"]["plan"]["sections"]) == 5
    assert fake_ai.calls


def test_generate_quick_depth(client: TestClient, fake_ai: FakeOpenRouter) -> None:
    fake_ai.content = _sample_ai_json(sections=3)
    run = _create_run(client, depth="quick")
    body = client.post(f"/api/v1/runs/{run['id']}/plan/generate").json()
    assert len(body["data"]["plan"]["sections"]) == 3


def test_generate_standard_depth(client: TestClient, fake_ai: FakeOpenRouter) -> None:
    fake_ai.content = _sample_ai_json(sections=5)
    run = _create_run(client, depth="standard")
    body = client.post(f"/api/v1/runs/{run['id']}/plan/generate").json()
    assert len(body["data"]["plan"]["sections"]) == 5


def test_generate_deep_depth(client: TestClient, fake_ai: FakeOpenRouter) -> None:
    fake_ai.content = _sample_ai_json(sections=8)
    run = _create_run(client, depth="deep")
    body = client.post(f"/api/v1/runs/{run['id']}/plan/generate").json()
    assert len(body["data"]["plan"]["sections"]) == 8


def test_generate_invalid_run(client: TestClient) -> None:
    response = client.post(
        "/api/v1/runs/00000000-0000-0000-0000-000000000001/plan/generate"
    )
    assert response.status_code == 404


def test_get_plan(client: TestClient) -> None:
    run = _create_run(client)
    client.post(f"/api/v1/runs/{run['id']}/plan/generate")
    response = client.get(f"/api/v1/runs/{run['id']}/plan")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["run"]["id"] == run["id"]
    assert data["title"]
    assert data["sections"]
    assert data["status"] == "plan_ready"


def test_update_plan(client: TestClient) -> None:
    run = _create_run(client)
    client.post(f"/api/v1/runs/{run['id']}/plan/generate")
    payload = {
        "title": "Updated Plan",
        "summary": "Edited summary",
        "sections": [
            {
                "id": "section-1",
                "order_index": 1,
                "title": "Custom Intro",
                "description": "Edited",
                "sub_questions": ["Q1?"],
                "status": "pending",
            }
        ],
    }
    response = client.put(f"/api/v1/runs/{run['id']}/plan", json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["plan"]["title"] == "Updated Plan"
    assert len(response.json()["data"]["plan"]["sections"]) == 1


def test_improve_plan(client: TestClient, fake_ai: FakeOpenRouter) -> None:
    run = _create_run(client)
    client.post(f"/api/v1/runs/{run['id']}/plan/generate")
    fake_ai.content = _sample_ai_json(sections=5)
    response = client.post(
        f"/api/v1/runs/{run['id']}/plan/improve",
        json={"instructions": "Make the plan more technical"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["meta"]["generation_mode"] == "ai"


def test_approve_plan(client: TestClient) -> None:
    run = _create_run(client)
    client.post(f"/api/v1/runs/{run['id']}/plan/generate")
    response = client.post(f"/api/v1/runs/{run['id']}/plan/approve")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "approved"

    run_after = client.get(f"/api/v1/runs/{run['id']}").json()["data"]
    assert run_after["status"] == "approved"


def test_approve_without_plan(client: TestClient) -> None:
    run = _create_run(client)
    response = client.post(f"/api/v1/runs/{run['id']}/plan/approve")
    assert response.status_code == 404


def test_malformed_ai_response(
    client: TestClient,
    fake_ai: FakeOpenRouter,
) -> None:
    fake_ai.content = "this is not json at all"
    run = _create_run(client)
    response = client.post(f"/api/v1/runs/{run['id']}/plan/generate")
    assert response.status_code == 502
    assert response.json()["success"] is False


def test_normalize_caps_sections_by_depth() -> None:
    from app.schemas.plan import normalize_ai_plan

    raw = {
        "title": "T",
        "summary": "S",
        "sections": [
            {"title": f"S{i}", "sub_questions": ["a", "b", "c", "d", "e", "f"]}
            for i in range(10)
        ],
    }
    plan = normalize_ai_plan(raw, depth=ResearchDepth.quick, fallback_topic="T")
    assert len(plan.sections) == 3
    assert all(len(s.sub_questions) <= 3 for s in plan.sections)
