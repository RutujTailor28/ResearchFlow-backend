"""Domain and HTTP-facing exceptions."""

from typing import Any


class AppError(Exception):
    """Base application error with a safe client message."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(
        self,
        message: str = "Resource not found",
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=404, detail=detail)


class ValidationAppError(AppError):
    def __init__(
        self,
        message: str = "Validation failed",
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=422, detail=detail)


class ConflictError(AppError):
    def __init__(
        self,
        message: str = "Conflict",
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=409, detail=detail)


class ForbiddenError(AppError):
    def __init__(
        self,
        message: str = "Forbidden",
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=403, detail=detail)


class AiConfigError(AppError):
    def __init__(
        self,
        message: str = "AI provider is not configured",
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=503, detail=detail)


class AiProviderError(AppError):
    def __init__(
        self,
        message: str = "AI provider request failed",
        *,
        detail: dict[str, Any] | None = None,
        status_code: int = 502,
    ) -> None:
        super().__init__(message, status_code=status_code, detail=detail)


class AiTimeoutError(AppError):
    def __init__(
        self,
        message: str = "The AI request timed out. Please try again.",
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=504, detail=detail)


class AiParseError(AppError):
    def __init__(
        self,
        message: str = "The AI returned an invalid response.",
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=502, detail=detail)
