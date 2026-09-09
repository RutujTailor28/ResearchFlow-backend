"""Research run schemas and status enums."""

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class RunStatus(str, Enum):
    draft = "draft"
    plan_pending = "plan_pending"
    plan_ready = "plan_ready"
    approved = "approved"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    partial = "partial"


class ResearchDepth(str, Enum):
    quick = "quick"
    standard = "standard"
    deep = "deep"


class Audience(str, Enum):
    executive = "executive"
    technical = "technical"
    general = "general"


class Tone(str, Enum):
    professional = "professional"
    academic = "academic"
    conversational = "conversational"


class CreateRunRequest(BaseModel):
    topic: str = Field(
        ...,
        min_length=3,
        max_length=500,
        examples=["Future of AI in healthcare diagnostics"],
    )
    depth: ResearchDepth = Field(
        default=ResearchDepth.standard,
        description="Research depth: quick | standard | deep",
    )
    audience: Audience = Audience.technical
    tone: Tone = Tone.professional
    target_length: int = Field(default=3000, ge=500, le=20000)
    source_types: List[str] = Field(
        default_factory=lambda: ["official", "research", "news"]
    )
    cost_limit_usd: Optional[float] = Field(
        default=None,
        ge=0,
        description="Optional per-run cost ceiling in USD. Null uses RUN_COST_LIMIT_USD.",
    )

    @field_validator("topic")
    @classmethod
    def topic_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("topic must be at least 3 characters")
        return cleaned


class RunResponse(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None
    topic: str
    depth: ResearchDepth
    audience: str
    tone: str
    target_length: int
    source_types: List[str]
    status: RunStatus
    plan_json: Optional[dict[str, Any]] = None
    cost_usd: float = 0
    duration_ms: Optional[int] = None
    source_count: Optional[int] = None
    cancel_requested: bool = False
    cost_limit_usd: Optional[float] = None
    started_at: Optional[datetime] = None
    stop_reason: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


class RunStatusResponse(BaseModel):
    id: UUID
    status: RunStatus
    cancel_requested: bool = False
    stop_reason: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
    error: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "status": "running",
                    "cancel_requested": False,
                    "stop_reason": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "finished_at": None,
                    "error": None,
                }
            ]
        }
    }


class RunListResponse(BaseModel):
    items: List[RunResponse]
    total: int
    limit: int
    offset: int
