"""Finding schemas (Phase 5)."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateFindingRequest(BaseModel):
    section_id: UUID
    claim: str = Field(..., min_length=3, max_length=4000)
    source_id: Optional[UUID] = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    is_gap: bool = False

    @field_validator("claim")
    @classmethod
    def claim_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("claim must be at least 3 characters")
        return cleaned


class UpdateFindingRequest(BaseModel):
    claim: Optional[str] = Field(default=None, min_length=3, max_length=4000)
    source_id: Optional[UUID] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    is_gap: Optional[bool] = None
    clear_source: bool = False

    @field_validator("claim")
    @classmethod
    def claim_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("claim must be at least 3 characters")
        return cleaned


class FindingResponse(BaseModel):
    id: UUID
    run_id: UUID
    section_id: UUID
    claim: str
    source_id: Optional[UUID] = None
    confidence: float
    is_gap: bool = False
    created_at: datetime


class FindingListResponse(BaseModel):
    items: List[FindingResponse]
    total: int
