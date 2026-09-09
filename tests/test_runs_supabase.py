"""Live Supabase persistence tests for Phase 1 runs.

Skipped automatically when the `runs` table is not available.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import clear_settings_cache, get_settings
from app.core.supabase import is_supabase_configured, reset_supabase_clients
from app.main import app
from app.repositories.runs_repository import RunsRepository
from app.services import run_service as run_service_module


@pytest.fixture(scope="module")
def supabase_ready() -> RunsRepository:
    clear_settings_cache()
    reset_supabase_clients()
    settings = get_settings()
    if not is_supabase_configured(settings):
        pytest.skip("Supabase is not configured")
    repo = RunsRepository()
    if not repo.table_exists():
        pytest.skip("runs table missing — apply migrations/001_create_runs.sql")
    return repo


@pytest.fixture
def live_client(supabase_ready: RunsRepository):
    original = run_service_module.run_service._repo
    run_service_module.run_service._repo = supabase_ready
    with TestClient(app) as client:
        yield client
    run_service_module.run_service._repo = original


def test_create_and_persist_across_clients(live_client: TestClient) -> None:
    """Create with one client instance, read with a fresh repository (restart simulation)."""
    payload = {
        "topic": "Persistence check for AI healthcare research",
        "depth": "quick",
        "audience": "general",
        "tone": "conversational",
        "target_length": 1000,
        "source_types": ["news"],
    }
    created = live_client.post("/api/v1/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["data"]["id"]

    # New repository instance simulates process restart
    fresh = RunsRepository()
    fetched = fresh.get_by_id(__import__("uuid").UUID(run_id))
    assert fetched is not None
    assert fetched.topic == payload["topic"]
    assert fetched.status.value == "plan_pending"

    # Cleanup
    live_client.delete(f"/api/v1/runs/{run_id}")
    assert fresh.get_by_id(__import__("uuid").UUID(run_id)) is None


def test_live_status_cancel_delete_flow(live_client: TestClient) -> None:
    created = live_client.post(
        "/api/v1/runs",
        json={
            "topic": "Live cancel and delete flow topic",
            "depth": "standard",
        },
    )
    assert created.status_code == 200
    run_id = created.json()["data"]["id"]

    status = live_client.get(f"/api/v1/runs/{run_id}/status")
    assert status.status_code == 200
    assert status.json()["data"]["status"] == "plan_pending"

    cancelled = live_client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"
    assert cancelled.json()["data"]["finished_at"] is not None

    deleted = live_client.delete(f"/api/v1/runs/{run_id}")
    assert deleted.status_code == 200
    assert live_client.get(f"/api/v1/runs/{run_id}").status_code == 404
