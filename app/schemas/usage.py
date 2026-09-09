"""Usage, cost, history, and export schemas (Phase 9)."""

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.agent_event import AgentRole
from app.schemas.run import RunStatus


class ExportFormat(str, Enum):
    md = "md"
    pdf = "pdf"
    docx = "docx"


class AgentUsageItem(BaseModel):
    agent_name: str
    agent_role: Optional[AgentRole] = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    cost_usd: float = 0.0
    model: Optional[str] = None
    event_count: int = 0


class RunUsageResponse(BaseModel):
    run_id: UUID
    agents: List[AgentUsageItem] = Field(default_factory=list)
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    cost_by_agent: dict[str, float] = Field(default_factory=dict)
    models_used: List[str] = Field(default_factory=list)
    note: str = (
        "Costs and tokens are aggregated from stored agent_events. "
        "cost_usd is only counted when OpenRouter (or emitter) provided it — never invented."
    )


class RunCostResponse(BaseModel):
    run_id: UUID
    total_cost_usd: float = 0.0
    cost_by_agent: dict[str, float] = Field(default_factory=dict)
    cost_by_role: dict[str, float] = Field(default_factory=dict)
    agents: List[AgentUsageItem] = Field(default_factory=list)
    note: str = "cost_usd values come from recorded usage only; missing provider cost remains 0."


class RunHistoryItem(BaseModel):
    id: UUID
    topic: str
    status: RunStatus
    duration_ms: Optional[int] = None
    total_cost_usd: float = 0.0
    source_count: int = 0
    finding_count: int = 0
    created_at: datetime
    finished_at: Optional[datetime] = None


class TimelineEvent(BaseModel):
    """Replay-ready event (same stored agent_events, no duplicate storage)."""

    id: UUID
    timestamp: datetime
    agent_name: str
    agent_role: AgentRole
    event_type: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class RunHistoryDetailResponse(BaseModel):
    run: RunHistoryItem
    timeline: List[TimelineEvent] = Field(default_factory=list)
    timeline_count: int = 0
