"""Phase 1 run management API tests.

Uses a fake repository by default so tests run without a live database.
Integration persistence tests run when Supabase `runs` table is available.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.memory import store
from app.schemas.run import ResearchDepth, RunResponse, RunStatus
from app.services import run_service as run_service_module


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
        }
        self.rows[run_id] = created
        return self._to_response(created)

    def get_by_id(self, run_id: UUID) -> Optional[RunResponse]:
        row = self.rows.get(run_id)
        return self._to_response(row) if row else None

    def list(
        self,
        *,
        status: Optional[RunStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RunResponse], int]:
        items = list(self.rows.values())
        if status is not None:
            items = [r for r in items if r["status"] == status.value]
        items.sort(key=lambda r: r["created_at"], reverse=True)
        total = len(items)
        page = items[offset : offset + limit]
        return [self._to_response(r) for r in page], total

    def update(self, run_id: UUID, fields: dict[str, Any]) -> RunResponse:
        if run_id not in self.rows:
            from app.core.exceptions import NotFoundError

            raise NotFoundError("Run not found", detail={"run_id": str(run_id)})
        self.rows[run_id] = {**self.rows[run_id], **fields}
        return self._to_response(self.rows[run_id])

    def delete(self, run_id: UUID) -> None:
        if run_id not in self.rows:
            from app.core.exceptions import NotFoundError

            raise NotFoundError("Run not found", detail={"run_id": str(run_id)})
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
            user_id=UUID(str(row["user_id"])) if row.get("user_id") else None,
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


@pytest.fixture
def fake_repo() -> FakeRunsRepository:
    return FakeRunsRepository()


@pytest.fixture
def client(fake_repo: FakeRunsRepository):
    original = run_service_module.run_service._repo
    run_service_module.run_service._repo = fake_repo
    store.clear()
    with TestClient(app) as test_client:
        yield test_client
    run_service_module.run_service._repo = original
    store.clear()


def _create_run(client: TestClient, **overrides: object) -> dict:
    payload = {
        "topic": "Future of Artificial Intelligence in Healthcare",
        "depth": "standard",
        "audience": "technical",
        "tone": "professional",
        "target_length": 3000,
        "source_types": ["official", "research", "news"],
    }
    payload.update(overrides)
    response = client.post("/api/v1/runs", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    return body["data"]


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["service"] == "Multi-Agent Research API"
    assert "supabase" in body["data"]


def test_create_run(client: TestClient) -> None:
    run = _create_run(client)
    assert run["status"] == "plan_pending"
    assert run["topic"].startswith("Future of Artificial")
    assert run["cost_usd"] == 0
    assert run["finished_at"] is None
    assert run["user_id"] is None


def test_create_run_validation_topic_too_short(client: TestClient) -> None:
    response = client.post("/api/v1/runs", json={"topic": "ab", "depth": "standard"})
    assert response.status_code == 422
    assert response.json()["success"] is False


def test_create_run_invalid_depth(client: TestClient) -> None:
    response = client.post(
        "/api/v1/runs",
        json={"topic": "Valid research topic here", "depth": "ultra"},
    )
    assert response.status_code == 422


def test_list_runs(client: TestClient) -> None:
    _create_run(client, topic="Topic A about AI systems")
    _create_run(client, topic="Topic B about robotics")
    response = client.get("/api/v1/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["total"] == 2
    assert len(body["data"]["items"]) == 2


def test_list_runs_filter_status(client: TestClient) -> None:
    run = _create_run(client)
    client.post(f"/api/v1/runs/{run['id']}/cancel")
    response = client.get("/api/v1/runs", params={"status": "cancelled"})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["status"] == "cancelled"


def test_get_run(client: TestClient) -> None:
    created = _create_run(client)
    response = client.get(f"/api/v1/runs/{created['id']}")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == created["id"]


def test_get_run_status(client: TestClient) -> None:
    created = _create_run(client)
    response = client.get(f"/api/v1/runs/{created['id']}/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data.keys()) == {"id", "status", "created_at", "finished_at", "error"}
    assert data["status"] == "plan_pending"
    assert data["id"] == created["id"]


def test_get_run_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 404
    assert response.json()["success"] is False


def test_get_run_invalid_uuid(client: TestClient) -> None:
    response = client.get("/api/v1/runs/not-a-uuid")
    assert response.status_code == 422
    assert response.json()["success"] is False


def test_cancel_run(client: TestClient) -> None:
    created = _create_run(client)
    response = client.post(f"/api/v1/runs/{created['id']}/cancel")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "cancelled"
    assert data["finished_at"] is not None


def test_cancel_run_invalid_when_already_cancelled(client: TestClient) -> None:
    created = _create_run(client)
    assert client.post(f"/api/v1/runs/{created['id']}/cancel").status_code == 200
    response = client.post(f"/api/v1/runs/{created['id']}/cancel")
    assert response.status_code == 409
    assert response.json()["success"] is False


def test_cancel_run_not_found(client: TestClient) -> None:
    response = client.post("/api/v1/runs/00000000-0000-0000-0000-000000000001/cancel")
    assert response.status_code == 404


def test_delete_run(client: TestClient) -> None:
    created = _create_run(client)
    response = client.delete(f"/api/v1/runs/{created['id']}")
    assert response.status_code == 200
    assert response.json()["data"]["deleted"] is True
    assert client.get(f"/api/v1/runs/{created['id']}").status_code == 404


def test_delete_run_not_found(client: TestClient) -> None:
    response = client.delete("/api/v1/runs/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 404
