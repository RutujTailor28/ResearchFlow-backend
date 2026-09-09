"""Shared tool helpers: timeouts, retries, backoff."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

DEFAULT_TOOL_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 2  # retries after first attempt (Phase 10)
DEFAULT_BACKOFF_BASE_S = 0.5


class ToolError(AppError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, detail=detail or {})


class ToolTimeoutError(ToolError):
    def __init__(
        self,
        message: str = "Research tool timed out",
        *,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message, status_code=504, detail=detail)


class ToolValidationError(ToolError):
    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message, status_code=422, detail=detail)


def with_retries(
    operation: str,
    fn: Callable[[], T],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
) -> T:
    """
    Execute fn with exponential backoff on transient failures.
    max_retries = retries after the first attempt (default 2 → up to 3 tries).
    """
    last_error: Exception | None = None
    attempts = max(1, int(max_retries)) + 1  # initial + retries

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (httpx.TimeoutException, TimeoutError) as exc:
            last_error = exc
            logger.warning(
                "Tool timeout operation=%s attempt=%s/%s",
                operation,
                attempt,
                attempts,
            )
            if attempt >= attempts:
                raise ToolTimeoutError(
                    f"{operation} timed out after {attempts} attempts",
                    detail={"operation": operation, "attempts": attempts},
                ) from exc
        except httpx.HTTPStatusError as exc:
            last_error = exc
            status = exc.response.status_code
            if status not in {408, 425, 429, 500, 502, 503, 504} or attempt >= attempts:
                raise ToolError(
                    f"{operation} failed with HTTP {status}",
                    detail={"operation": operation, "http_status": status},
                ) from exc
            logger.warning(
                "Tool HTTP error operation=%s status=%s attempt=%s/%s",
                operation,
                status,
                attempt,
                attempts,
            )
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning(
                "Tool transport error operation=%s type=%s attempt=%s/%s",
                operation,
                type(exc).__name__,
                attempt,
                attempts,
            )
            if attempt >= attempts:
                raise ToolError(
                    f"{operation} failed",
                    detail={"operation": operation, "error": type(exc).__name__},
                ) from exc

        sleep_s = backoff_base_s * (2 ** (attempt - 1))
        time.sleep(sleep_s)

    raise ToolError(
        f"{operation} failed",
        detail={
            "operation": operation,
            "error": type(last_error).__name__ if last_error else None,
        },
    )
