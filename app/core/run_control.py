"""Cooperative run control: cancel, cost ceiling, timeouts (Phase 10)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class StopReason(str, Enum):
    none = "none"
    cancelled = "cancelled"
    cost_limit = "cost_limit"
    timeout = "timeout"


class RunAborted(Exception):
    """Raised when a worker should stop (cancel / cost / timeout)."""

    def __init__(self, reason: StopReason, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason.value)


@dataclass
class _RunControlState:
    cancel_requested: bool = False
    cost_usd: float = 0.0
    cost_limit_usd: Optional[float] = None
    deadline_monotonic: Optional[float] = None
    stop_reason: StopReason = StopReason.none
    started_monotonic: float = field(default_factory=time.monotonic)


class RunControlRegistry:
    """
    In-process cooperative control for active runs.
    Cancel flag is also persisted on the runs row for durability.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[UUID, _RunControlState] = {}

    def begin(
        self,
        run_id: UUID,
        *,
        cost_limit_usd: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> None:
        settings = get_settings()
        limit = cost_limit_usd
        if limit is None:
            limit = settings.run_cost_limit_usd
        timeout = timeout_s if timeout_s is not None else settings.run_timeout_s
        with self._lock:
            now = time.monotonic()
            self._states[run_id] = _RunControlState(
                cancel_requested=False,
                cost_usd=0.0,
                cost_limit_usd=float(limit) if limit is not None else None,
                deadline_monotonic=(now + float(timeout)) if timeout and timeout > 0 else None,
                stop_reason=StopReason.none,
                started_monotonic=now,
            )

    def end(self, run_id: UUID) -> None:
        with self._lock:
            self._states.pop(run_id, None)

    def request_cancel(self, run_id: UUID) -> None:
        with self._lock:
            state = self._states.get(run_id)
            if state is None:
                self._states[run_id] = _RunControlState(
                    cancel_requested=True,
                    stop_reason=StopReason.cancelled,
                )
            else:
                state.cancel_requested = True
                state.stop_reason = StopReason.cancelled
        logger.info("Cancel requested for run_id=%s", run_id)

    def add_cost(self, run_id: UUID, amount: float) -> float:
        if amount <= 0:
            return self.current_cost(run_id)
        with self._lock:
            state = self._ensure(run_id)
            state.cost_usd = round(state.cost_usd + float(amount), 6)
            return state.cost_usd

    def current_cost(self, run_id: UUID) -> float:
        with self._lock:
            state = self._states.get(run_id)
            return float(state.cost_usd) if state else 0.0

    def is_cancel_requested(self, run_id: UUID) -> bool:
        with self._lock:
            state = self._states.get(run_id)
            return bool(state and state.cancel_requested)

    def stop_reason(self, run_id: UUID) -> StopReason:
        with self._lock:
            state = self._states.get(run_id)
            return state.stop_reason if state else StopReason.none

    def check(self, run_id: UUID, *, db_cancel: bool = False) -> StopReason:
        """
        Return a non-none StopReason if work should halt.
        Does not raise — callers decide whether to raise RunAborted.
        """
        with self._lock:
            state = self._ensure(run_id)
            if db_cancel or state.cancel_requested:
                state.cancel_requested = True
                state.stop_reason = StopReason.cancelled
                return StopReason.cancelled

            if (
                state.deadline_monotonic is not None
                and time.monotonic() >= state.deadline_monotonic
            ):
                state.stop_reason = StopReason.timeout
                return StopReason.timeout

            if (
                state.cost_limit_usd is not None
                and state.cost_limit_usd > 0
                and state.cost_usd >= state.cost_limit_usd
            ):
                state.stop_reason = StopReason.cost_limit
                return StopReason.cost_limit

            return StopReason.none

    def assert_can_continue(self, run_id: UUID, *, db_cancel: bool = False) -> None:
        reason = self.check(run_id, db_cancel=db_cancel)
        if reason != StopReason.none:
            raise RunAborted(reason)

    def _ensure(self, run_id: UUID) -> _RunControlState:
        state = self._states.get(run_id)
        if state is None:
            settings = get_settings()
            now = time.monotonic()
            timeout = settings.run_timeout_s
            state = _RunControlState(
                cost_limit_usd=settings.run_cost_limit_usd,
                deadline_monotonic=(now + float(timeout)) if timeout and timeout > 0 else None,
                started_monotonic=now,
            )
            self._states[run_id] = state
        return state


run_control = RunControlRegistry()
