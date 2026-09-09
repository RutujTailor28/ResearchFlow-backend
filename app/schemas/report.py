"""Report schemas (Phase 7)."""

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class RegenerateSectionRequest(BaseModel):
    instructions: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("instructions")
    @classmethod
    def clean_instructions(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class UpdateReportRequest(BaseModel):
    markdown: str = Field(..., min_length=1, max_length=500000)

    @field_validator("markdown")
    @classmethod
    def markdown_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("markdown must not be blank")
        return cleaned


class ReportSectionBlock(BaseModel):
    section_id: UUID
    title: str
    markdown: str
    confidence: float = Field(default=0.5, ge=0, le=1)
    citation_indices: List[int] = Field(default_factory=list)
    gap_notes: List[str] = Field(default_factory=list)


class ReportResponse(BaseModel):
    id: UUID
    run_id: UUID
    markdown: str
    sections_json: List[dict[str, Any]] = Field(default_factory=list)
    sections_confidence_json: Optional[dict[str, Any]] = None
    unverified_claims_json: Optional[list[Any]] = None
    citation_map_json: dict[str, Any] = Field(default_factory=dict)
    conflicts_json: Optional[list[Any]] = None
    verification_json: Optional[dict[str, Any]] = None
    verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ReportGenerateResponse(BaseModel):
    report: ReportResponse
    sections_written: int
    citations_used: int
    gaps_noted: int
