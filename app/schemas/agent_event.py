"""Agent event and observability schemas (Phase 6)."""

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    planner = "planner"
    researcher = "researcher"
    writer = "writer"
    verifier = "verifier"
    orchestrator = "orchestrator"


class AgentEventType(str, Enum):
    agent_started = "agent_started"
    thinking = "thinking"
    tool_call = "tool_call"
    tool_result = "tool_result"
    agent_finished = "agent_finished"
    error = "error"
    retry = "retry"
    writer_started = "writer_started"
    section_started = "section_started"
    section_completed = "section_completed"
    writer_finished = "writer_finished"
    verifier_started = "verifier_started"
    verifier_finished = "verifier_finished"


class AgentRuntimeStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    idle = "idle"


class AgentEventResponse(BaseModel):
    id: UUID
    run_id: UUID
    agent_name: str
    agent_role: AgentRole
    event_type: AgentEventType
    payload_json: dict[str, Any] = Field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0
    created_at: datetime


class AgentEventListResponse(BaseModel):
    items: List[AgentEventResponse]
    total: int
    limit: int
    offset: int


class AgentStatusResponse(BaseModel):
    agent_name: str
    agent_role: AgentRole
    status: AgentRuntimeStatus
    last_event_type: Optional[AgentEventType] = None
    last_event_at: Optional[datetime] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0
    event_count: int = 0
    error: Optional[str] = None
    current_action: Optional[str] = None


class AgentListResponse(BaseModel):
    items: List[AgentStatusResponse]
    total: int


class AgentsStatusSummaryResponse(BaseModel):
    run_id: UUID
    run_status: str
    agents: List[AgentStatusResponse]
    total_events: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
