"""In-memory store for Phase 1. Replace with a DB-backed repository later."""

from __future__ import annotations

from copy import deepcopy
from threading import Lock
from typing import Dict, List, Optional
from uuid import UUID

from app.schemas.plan import ResearchPlan
from app.schemas.run import RunResponse, RunStatus


class InMemoryStore:
    """Thread-safe temporary storage for runs and plans."""

    def __init__(self) -> None:
        self._runs: Dict[UUID, RunResponse] = {}
        self._plans: Dict[UUID, ResearchPlan] = {}
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._plans.clear()

    def save_run(self, run: RunResponse) -> RunResponse:
        with self._lock:
            self._runs[run.id] = run.model_copy(deep=True)
            return self._runs[run.id].model_copy(deep=True)

    def get_run(self, run_id: UUID) -> Optional[RunResponse]:
        with self._lock:
            run = self._runs.get(run_id)
            return run.model_copy(deep=True) if run else None

    def list_runs(
        self,
        *,
        status: Optional[RunStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[List[RunResponse], int]:
        with self._lock:
            items = list(self._runs.values())
            if status is not None:
                items = [r for r in items if r.status == status]
            items.sort(key=lambda r: r.created_at, reverse=True)
            total = len(items)
            page = items[offset : offset + limit]
            return [r.model_copy(deep=True) for r in page], total

    def save_plan(self, run_id: UUID, plan: ResearchPlan) -> ResearchPlan:
        with self._lock:
            self._plans[run_id] = plan.model_copy(deep=True)
            return deepcopy(self._plans[run_id])

    def get_plan(self, run_id: UUID) -> Optional[ResearchPlan]:
        with self._lock:
            plan = self._plans.get(run_id)
            return plan.model_copy(deep=True) if plan else None

    def delete_plan(self, run_id: UUID) -> None:
        with self._lock:
            self._plans.pop(run_id, None)


# Process-wide store for Phase 1. Tests should call store.clear().
store = InMemoryStore()
