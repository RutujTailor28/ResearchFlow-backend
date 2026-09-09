"""Legal run status transitions (Phase 10)."""

from __future__ import annotations

from app.core.exceptions import ConflictError
from app.schemas.run import RunStatus

# Happy path: plan_pending → plan_ready → approved → running → completed
# Alternatives from running: failed | cancelled | partial
# Retry: partial|failed|completed → running
ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.draft: {RunStatus.plan_pending, RunStatus.cancelled},
    RunStatus.plan_pending: {
        RunStatus.plan_ready,
        RunStatus.cancelled,
        RunStatus.failed,
    },
    RunStatus.plan_ready: {
        RunStatus.plan_ready,  # plan edit / improve stays ready
        RunStatus.approved,
        RunStatus.plan_pending,  # regenerate from scratch
        RunStatus.cancelled,
        RunStatus.failed,
    },
    RunStatus.approved: {
        RunStatus.running,
        RunStatus.plan_ready,  # unapprove / re-edit (if supported)
        RunStatus.cancelled,
        RunStatus.failed,
    },
    RunStatus.running: {
        RunStatus.completed,
        RunStatus.failed,
        RunStatus.cancelled,
        RunStatus.partial,
    },
    RunStatus.partial: {
        RunStatus.running,  # retry
        RunStatus.cancelled,
    },
    RunStatus.failed: {
        RunStatus.running,  # retry
        RunStatus.cancelled,
    },
    RunStatus.completed: {
        RunStatus.running,  # optional re-research / retry failed tasks
        RunStatus.cancelled,
    },
    RunStatus.cancelled: set(),  # terminal
}


def can_transition(current: RunStatus, new: RunStatus) -> bool:
    if current == new:
        return True
    return new in ALLOWED_TRANSITIONS.get(current, set())


def assert_transition(current: RunStatus, new: RunStatus, *, run_id: str | None = None) -> None:
    if can_transition(current, new):
        return
    detail: dict = {
        "from": current.value,
        "to": new.value,
        "allowed": sorted(s.value for s in ALLOWED_TRANSITIONS.get(current, set())),
    }
    if run_id:
        detail["run_id"] = run_id
    raise ConflictError(
        f"Invalid status transition '{current.value}' → '{new.value}'",
        detail=detail,
    )
