"""Shared API response envelope and common types."""

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Consistent success envelope for API responses."""

    success: bool = True
    message: str = "Operation completed successfully"
    data: Optional[T] = None


class APIErrorResponse(BaseModel):
    """Consistent error envelope for API responses."""

    success: bool = False
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


def success_response(
    data: Any = None,
    message: str = "Operation completed successfully",
) -> dict[str, Any]:
    return {"success": True, "message": message, "data": data}


def error_response(
    message: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"success": False, "message": message, "detail": detail or {}}
