"""Phase 4 research tools + source management tests."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ConflictError, NotFoundError
from app.main import app
from app.schemas.run import ResearchDepth, RunResponse, RunStatus
from app.schemas.source import SourceResponse
from app.services import run_service as run_service_module
from app.services import source_service as source_service_module
from app.services.run_service import RunService
from app.services.source_service import SourceService
from app.tools.calculator import calculate
from app.tools.extract import extract_readable_content
from app.tools.fetch import validate_http_url
from app.tools.quality import score_source_quality
from app.tools.search.base import SearchResponse, SearchResultItem


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
        return self._to_response(created)

    def get_by_id(self, run_id: UUID) -> Optional[RunResponse]:
        row = self.rows.get(run_id)
        return self._to_response(row) if row else None

    def list(self, **kwargs: Any) -> tuple[list[RunResponse], int]:
        items = [self._to_response(r) for r in self.rows.values()]
        return items, len(items)

    def update(self, run_id: UUID, fields: dict[str, Any]) -> RunResponse:
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


class FakeSourcesRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, SourceResponse] = {}

    def table_exists(self) -> bool:
        return True

    def list_for_run(self, run_id: UUID) -> list[SourceResponse]:
        items = [r for r in self.rows.values() if r.run_id == run_id]
        items.sort(key=lambda r: r.quality_score, reverse=True)
        return items

    def get_for_run(self, run_id: UUID, source_id: UUID) -> SourceResponse:
        row = self.rows.get(source_id)
        if row is None or row.run_id != run_id:
            raise NotFoundError("Source not found for this run")
        return row

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

    def create(self, row: dict[str, Any]) -> SourceResponse:
        source_id = UUID(str(row["id"]))
        if self.find_by_url(UUID(str(row["run_id"])), row["url"]):
            raise ConflictError("Duplicate source for this run")
        if self.find_by_content_hash(UUID(str(row["run_id"])), row["content_hash"]):
            raise ConflictError("Duplicate source for this run")
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

    def delete(self, run_id: UUID, source_id: UUID) -> None:
        self.get_for_run(run_id, source_id)
        del self.rows[source_id]


class FakeSearchProvider:
    name = "fake"

    def search(self, query: str, *, max_results: int = 5) -> SearchResponse:
        return SearchResponse(
            query=query,
            provider=self.name,
            results=[
                SearchResultItem(
                    title="Example NIH Page",
                    url="https://www.nih.gov/example",
                    snippet="Official research summary",
                    source="fake",
                )
            ][:max_results],
        )


@pytest.fixture
def fake_runs() -> FakeRunsRepository:
    return FakeRunsRepository()


@pytest.fixture
def fake_sources() -> FakeSourcesRepository:
    return FakeSourcesRepository()


@pytest.fixture
def client(fake_runs: FakeRunsRepository, fake_sources: FakeSourcesRepository, monkeypatch):
    runs = RunService(repository=fake_runs)
    sources = SourceService(runs=runs, sources=fake_sources)

    from app.api.v1 import research as research_api
    from app.api.v1 import runs as runs_api
    from app.api.v1 import sources as sources_api
    from app.services import research_tool_service as research_mod

    # Patch search provider
    monkeypatch.setattr(
        research_mod,
        "get_search_provider",
        lambda name=None: FakeSearchProvider(),
    )

    # Patch fetch/extract used by source + research services
    class FakeFetch:
        url = "https://www.nih.gov/example"
        final_url = "https://www.nih.gov/example"
        status_code = 200
        content_type = "text/html"
        html = (
            "<html><head><title>NIH Example</title></head>"
            "<body><article><p>" + ("Clinical research findings. " * 40) + "</p></article></body></html>"
        )
        headers = {}

    monkeypatch.setattr(
        "app.services.source_service.fetch_page",
        lambda url, timeout_s=30, max_retries=3: FakeFetch(),
    )
    monkeypatch.setattr(
        "app.services.research_tool_service.fetch_page",
        lambda url, timeout_s=30, max_retries=3: FakeFetch(),
    )
    monkeypatch.setattr(
        "app.services.source_service.validate_http_url",
        lambda url: url.strip(),
    )

    original_run = run_service_module.run_service
    original_source = source_service_module.source_service
    original_runs_api = runs_api.run_service
    original_sources_api = sources_api.source_service

    run_service_module.run_service = runs
    source_service_module.source_service = sources
    runs_api.run_service = runs
    sources_api.source_service = sources

    with TestClient(app) as test_client:
        yield test_client

    run_service_module.run_service = original_run
    source_service_module.source_service = original_source
    runs_api.run_service = original_runs_api
    sources_api.source_service = original_sources_api


def _create_run(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/runs",
        json={
            "topic": "Phase 4 source management healthcare AI topic",
            "depth": "standard",
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_search_abstraction(client: TestClient) -> None:
    response = client.post(
        "/api/v1/research/search",
        json={"query": "AI healthcare NIH", "max_results": 3},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["provider"] == "fake"
    assert data["results"]
    assert data["results"][0]["url"].startswith("http")


def test_fetch_tool(client: TestClient) -> None:
    response = client.post(
        "/api/v1/research/fetch",
        json={"url": "https://www.nih.gov/example"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status_code"] == 200
    assert data["html_length"] > 0


def test_extract_tool(client: TestClient) -> None:
    html = "<html><head><title>Demo</title></head><body><p>" + ("Alpha beta. " * 30) + "</p></body></html>"
    response = client.post(
        "/api/v1/research/extract",
        json={"html": html},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["title"] == "Demo"
    assert data["word_count"] > 10
    assert len(data["content_hash"]) == 64


def test_extract_from_url(client: TestClient) -> None:
    response = client.post(
        "/api/v1/research/extract",
        json={"url": "https://www.nih.gov/example"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["word_count"] > 10


def test_invalid_url_validation() -> None:
    with pytest.raises(Exception):
        validate_http_url("not-a-url")


def test_calculator_utility() -> None:
    assert calculate("2 + 3 * 4") == 14.0
    assert calculate("(10 - 2) / 4") == 2.0


def test_quality_scoring_prefers_gov() -> None:
    gov = score_source_quality(
        url="https://www.nih.gov/news",
        domain="nih.gov",
        title="Official update",
        extract_text=("word " * 300),
    )
    farm = score_source_quality(
        url="https://something.blogspot.com/post",
        domain="something.blogspot.com",
        title="Buy now sponsored tips",
        extract_text=("word " * 50),
    )
    assert gov.score > farm.score


def test_source_crud_and_duplicates(client: TestClient, fake_sources: FakeSourcesRepository) -> None:
    run = _create_run(client)

    created = client.post(
        f"/api/v1/runs/{run['id']}/sources",
        json={"url": "https://www.nih.gov/example"},
    )
    assert created.status_code == 200, created.text
    source = created.json()["data"]["source"]
    assert source["domain"] == "nih.gov"
    assert source["quality_score"] > 0
    assert source["content_hash"]

    listed = client.get(f"/api/v1/runs/{run['id']}/sources")
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1

    fetched = client.get(f"/api/v1/runs/{run['id']}/sources/{source['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["id"] == source["id"]

    # Duplicate URL
    dup_url = client.post(
        f"/api/v1/runs/{run['id']}/sources",
        json={"url": "https://www.nih.gov/example"},
    )
    assert dup_url.status_code == 409

    # Duplicate content hash (same extract from different URL) — inject via repo
    text = source["extract_text"]
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert fake_sources.find_by_content_hash(UUID(run["id"]), content_hash) is not None

    # Simulate second add that hits hash conflict by pre-inserting different url same hash
    # Direct service path: monkeypatch already returns same HTML so same hash for another URL
    # First delete, then create with different URL still same fake HTML → hash conflict after re-add? 
    # After one source exists, another URL with same HTML should conflict on hash.
    dup_hash = client.post(
        f"/api/v1/runs/{run['id']}/sources",
        json={"url": "https://www.nih.gov/example-2"},
    )
    assert dup_hash.status_code == 409
    assert "identical content" in dup_hash.json()["message"].lower() or dup_hash.json()["success"] is False

    deleted = client.delete(f"/api/v1/runs/{run['id']}/sources/{source['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/runs/{run['id']}/sources").json()["data"]["total"] == 0


def test_extract_readable_content_unit() -> None:
    html = "<html><head><title>Hello</title></head><body><script>x</script><p>One two three four five six seven eight nine ten eleven twelve.</p></body></html>"
    result = extract_readable_content(html)
    assert result.title == "Hello"
    assert "script" not in result.text.lower()
    assert result.word_count >= 10
