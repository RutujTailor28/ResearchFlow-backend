"""Phase 3 section CRUD / reorder tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import NotFoundError, ValidationAppError
from app.main import app
from app.schemas.plan import PlanSection, ResearchPlan, SectionStatus
from app.schemas.run import ResearchDepth, RunResponse, RunStatus
from app.schemas.section import SectionResponse
from app.services import run_service as run_service_module
from app.services import section_service as section_service_module
from app.services.run_service import RunService
from app.services.section_service import SectionService


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
            "user_id": row.get("user_id"),
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
        if run_id not in self.rows:
            raise NotFoundError("Run not found")
        self.rows[run_id] = {**self.rows[run_id], **fields}
        return self._to_response(self.rows[run_id])

    def delete(self, run_id: UUID) -> None:
        self.rows.pop(run_id, None)

    def table_exists(self) -> bool:
        return True

    @staticmethod
    def _to_response(row: dict[str, Any]) -> RunResponse:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
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
            finished_at=None,
        )


class FakePlanSectionsRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, SectionResponse] = {}

    def list_records_for_run(self, run_id: UUID) -> list[SectionResponse]:
        items = [r for r in self.rows.values() if r.run_id == run_id]
        items.sort(key=lambda r: r.order_index)
        return items

    def list_for_run(self, run_id: UUID) -> list[PlanSection]:
        return [
            PlanSection(
                id=str(r.id),
                order_index=r.order_index,
                title=r.title,
                description=r.description,
                sub_questions=list(r.sub_questions),
                status=r.status,
            )
            for r in self.list_records_for_run(run_id)
        ]

    def get_for_run(self, run_id: UUID, section_id: UUID) -> SectionResponse:
        row = self.rows.get(section_id)
        if row is None or row.run_id != run_id:
            raise NotFoundError(
                "Section not found for this run",
                detail={"run_id": str(run_id), "section_id": str(section_id)},
            )
        return row

    def create(self, **kwargs: Any) -> SectionResponse:
        section_id = uuid4()
        now = datetime.now(timezone.utc)
        record = SectionResponse(
            id=section_id,
            run_id=kwargs["run_id"],
            order_index=kwargs["order_index"],
            title=kwargs["title"],
            description=kwargs["description"],
            sub_questions=list(kwargs["sub_questions"]),
            status=kwargs["status"],
            created_at=now,
            updated_at=now,
        )
        self.rows[section_id] = record
        return record

    def update_fields(
        self, *, run_id: UUID, section_id: UUID, fields: dict[str, Any]
    ) -> SectionResponse:
        current = self.get_for_run(run_id, section_id)
        data = current.model_dump()
        data.update(fields)
        if "status" in fields and isinstance(fields["status"], str):
            data["status"] = SectionStatus(fields["status"])
        data["updated_at"] = datetime.now(timezone.utc)
        updated = SectionResponse(**data)
        self.rows[section_id] = updated
        return updated

    def delete(self, *, run_id: UUID, section_id: UUID) -> None:
        self.get_for_run(run_id, section_id)
        del self.rows[section_id]

    def delete_for_run(self, run_id: UUID) -> None:
        for sid in [s.id for s in self.list_records_for_run(run_id)]:
            del self.rows[sid]

    def replace_for_run(self, run_id: UUID, plan: ResearchPlan) -> list[PlanSection]:
        self.delete_for_run(run_id)
        out: list[PlanSection] = []
        for section in plan.sections:
            created = self.create(
                run_id=run_id,
                order_index=section.order_index,
                title=section.title,
                description=section.description,
                sub_questions=section.sub_questions,
                status=section.status,
            )
            out.append(
                PlanSection(
                    id=str(created.id),
                    order_index=created.order_index,
                    title=created.title,
                    description=created.description,
                    sub_questions=created.sub_questions,
                    status=created.status,
                )
            )
        return out

    def max_order_index(self, run_id: UUID) -> int:
        items = self.list_records_for_run(run_id)
        return max((i.order_index for i in items), default=0)

    def reindex_run(self, run_id: UUID) -> list[SectionResponse]:
        items = self.list_records_for_run(run_id)
        for index, item in enumerate(items, start=1):
            self.rows[item.id] = item.model_copy(
                update={"order_index": index, "updated_at": datetime.now(timezone.utc)}
            )
        return self.list_records_for_run(run_id)

    def reorder(self, run_id: UUID, section_ids: list[UUID]) -> list[SectionResponse]:
        existing = self.list_records_for_run(run_id)
        existing_ids = {r.id for r in existing}
        if len(section_ids) != len(existing_ids):
            raise ValidationAppError("section_ids must include every section")
        missing = [sid for sid in section_ids if sid not in existing_ids]
        if missing:
            raise ValidationAppError(
                "One or more section_ids do not belong to this run",
                detail={"invalid_section_ids": [str(m) for m in missing]},
            )
        for index, sid in enumerate(section_ids, start=1):
            current = self.rows[sid]
            self.rows[sid] = current.model_copy(
                update={"order_index": index, "updated_at": datetime.now(timezone.utc)}
            )
        return self.list_records_for_run(run_id)

    def shift_orders_up(self, run_id: UUID, from_index: int) -> None:
        for item in self.list_records_for_run(run_id):
            if item.order_index >= from_index:
                self.rows[item.id] = item.model_copy(
                    update={"order_index": item.order_index + 1}
                )


@pytest.fixture
def fake_runs() -> FakeRunsRepository:
    return FakeRunsRepository()


@pytest.fixture
def fake_sections() -> FakePlanSectionsRepository:
    return FakePlanSectionsRepository()


@pytest.fixture
def client(fake_runs: FakeRunsRepository, fake_sections: FakePlanSectionsRepository):
    runs = RunService(repository=fake_runs)
    sections = SectionService(runs=runs, sections=fake_sections)

    from app.api.v1 import runs as runs_api
    from app.api.v1 import sections as sections_api

    original_run_mod = run_service_module.run_service
    original_sec_mod = section_service_module.section_service
    original_run_api = runs_api.run_service
    original_sec_api = sections_api.section_service

    run_service_module.run_service = runs
    section_service_module.section_service = sections
    runs_api.run_service = runs
    sections_api.section_service = sections

    with TestClient(app) as test_client:
        yield test_client

    run_service_module.run_service = original_run_mod
    section_service_module.section_service = original_sec_mod
    runs_api.run_service = original_run_api
    sections_api.section_service = original_sec_api


def _create_run(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/runs",
        json={
            "topic": "Section management test topic for AI healthcare",
            "depth": "standard",
            "audience": "technical",
            "tone": "professional",
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def _create_section(client: TestClient, run_id: str, title: str, **extra: Any) -> dict:
    payload = {"title": title, "description": f"Desc for {title}", "sub_questions": ["Q?"]}
    payload.update(extra)
    response = client.post(f"/api/v1/runs/{run_id}/sections", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_section_crud_flow(client: TestClient) -> None:
    run = _create_run(client)
    created = _create_section(client, run["id"], "Intro")
    assert created["order_index"] == 1
    assert created["status"] == "pending"
    assert created["run_id"] == run["id"]

    listed = client.get(f"/api/v1/runs/{run['id']}/sections").json()["data"]
    assert listed["total"] == 1

    fetched = client.get(
        f"/api/v1/runs/{run['id']}/sections/{created['id']}"
    ).json()["data"]
    assert fetched["title"] == "Intro"

    updated = client.put(
        f"/api/v1/runs/{run['id']}/sections/{created['id']}",
        json={
            "title": "Introduction",
            "description": "Updated",
            "sub_questions": ["A?", "B?"],
            "status": "researched",
        },
    ).json()["data"]
    assert updated["title"] == "Introduction"
    assert updated["status"] == "researched"
    assert updated["sub_questions"] == ["A?", "B?"]


def test_section_belongs_to_run(client: TestClient) -> None:
    run_a = _create_run(client)
    run_b = _create_run(client)
    section = _create_section(client, run_a["id"], "Only A")

    response = client.get(f"/api/v1/runs/{run_b['id']}/sections/{section['id']}")
    assert response.status_code == 404
    assert response.json()["success"] is False


def test_invalid_section_id(client: TestClient) -> None:
    run = _create_run(client)
    response = client.get(
        f"/api/v1/runs/{run['id']}/sections/00000000-0000-0000-0000-000000000099"
    )
    assert response.status_code == 404


def test_reorder_and_duplicates(client: TestClient) -> None:
    run = _create_run(client)
    s1 = _create_section(client, run["id"], "One")
    s2 = _create_section(client, run["id"], "Two")
    s3 = _create_section(client, run["id"], "Three")

    reordered = client.put(
        f"/api/v1/runs/{run['id']}/sections/reorder",
        json={"section_ids": [s3["id"], s1["id"], s2["id"]]},
    )
    assert reordered.status_code == 200, reordered.text
    items = reordered.json()["data"]["items"]
    assert [i["title"] for i in items] == ["Three", "One", "Two"]
    assert [i["order_index"] for i in items] == [1, 2, 3]

    dup = client.put(
        f"/api/v1/runs/{run['id']}/sections/reorder",
        json={"section_ids": [s1["id"], s1["id"], s2["id"]]},
    )
    assert dup.status_code == 422


def test_reorder_rejects_foreign_section(client: TestClient) -> None:
    run_a = _create_run(client)
    run_b = _create_run(client)
    a1 = _create_section(client, run_a["id"], "A1")
    b1 = _create_section(client, run_b["id"], "B1")

    response = client.put(
        f"/api/v1/runs/{run_a['id']}/sections/reorder",
        json={"section_ids": [b1["id"], a1["id"]]},
    )
    assert response.status_code == 422
    assert response.json()["success"] is False


def test_delete_section_reindexes(client: TestClient) -> None:
    run = _create_run(client)
    s1 = _create_section(client, run["id"], "One")
    s2 = _create_section(client, run["id"], "Two")
    s3 = _create_section(client, run["id"], "Three")

    deleted = client.delete(f"/api/v1/runs/{run['id']}/sections/{s2['id']}")
    assert deleted.status_code == 200

    items = client.get(f"/api/v1/runs/{run['id']}/sections").json()["data"]["items"]
    assert [i["title"] for i in items] == ["One", "Three"]
    assert [i["order_index"] for i in items] == [1, 2]
    assert s1["id"] == items[0]["id"]
    assert s3["id"] == items[1]["id"]


def test_insert_at_order_index(client: TestClient) -> None:
    run = _create_run(client)
    _create_section(client, run["id"], "One")
    _create_section(client, run["id"], "Three")
    inserted = _create_section(client, run["id"], "Two", order_index=2)
    assert inserted["order_index"] == 2
    titles = [
        i["title"]
        for i in client.get(f"/api/v1/runs/{run['id']}/sections").json()["data"]["items"]
    ]
    assert titles == ["One", "Two", "Three"]


def test_auth_ready_ownership_check(
    fake_runs: FakeRunsRepository,
    fake_sections: FakePlanSectionsRepository,
) -> None:
    owner = uuid4()
    other = uuid4()
    runs = RunService(repository=fake_runs)
    service = SectionService(runs=runs, sections=fake_sections)

    run = runs.create_run(
        __import__("app.schemas.run", fromlist=["CreateRunRequest"]).CreateRunRequest(
            topic="Owned run topic for auth check",
            depth=ResearchDepth.quick,
        )
    )
    # Simulate owned run
    fake_runs.rows[run.id]["user_id"] = str(owner)

    from app.core.exceptions import ForbiddenError

    with pytest.raises(ForbiddenError):
        service.list_sections(run.id, user_id=other)

    # Owner can access
    result = service.list_sections(run.id, user_id=owner)
    assert result.total == 0
