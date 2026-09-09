"""Research orchestration schemas (Phase 5)."""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ResearchTaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class ResearchTaskResponse(BaseModel):
    id: UUID
    run_id: UUID
    section_id: UUID
    sub_question: str
    status: ResearchTaskStatus
    tool_calls: int = 0
    error: Optional[str] = None
    gap_note: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


class ResearchStartResponse(BaseModel):
    run_id: UUID
    status: str
    task_count: int
    message: str


class ResearchStatusResponse(BaseModel):
    run_id: UUID
    run_status: str
    total_tasks: int
    pending: int
    running: int
    completed: int
    failed: int
    findings_count: int
    tasks: List[ResearchTaskResponse] = Field(default_factory=list)


class ResearchResultsResponse(BaseModel):
    run_id: UUID
    run_status: str
    findings_count: int
    gaps_count: int
    tasks: List[ResearchTaskResponse]
    findings: List[dict] = Field(default_factory=list)
