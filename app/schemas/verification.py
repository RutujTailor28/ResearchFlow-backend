"""Verification / quality schemas (Phase 8)."""

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SectionConfidenceLabel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class UnverifiedClaim(BaseModel):
    claim: str
    section_id: Optional[UUID] = None
    section_title: Optional[str] = None
    reason: str
    suggested_evidence_gap: str


class SourceConflict(BaseModel):
    id: str
    section_id: Optional[UUID] = None
    section_title: Optional[str] = None
    statement: str
    source_a: dict[str, Any]
    source_b: dict[str, Any]
    explanation: str
    finding_ids: List[str] = Field(default_factory=list)


class RepeatedContent(BaseModel):
    text_a: str
    text_b: str
    section_a: Optional[str] = None
    section_b: Optional[str] = None
    similarity: float


class SectionVerification(BaseModel):
    section_id: Optional[UUID] = None
    section_title: str
    confidence: SectionConfidenceLabel
    verified_claims: int = 0
    unverified_claims: int = 0
    supporting_source_ids: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class VerificationResponse(BaseModel):
    run_id: UUID
    report_id: UUID
    status: str = "completed"
    verified_at: Optional[datetime] = None
    overall_confidence: SectionConfidenceLabel = SectionConfidenceLabel.medium
    sections: List[SectionVerification] = Field(default_factory=list)
    unverified_claims: List[UnverifiedClaim] = Field(default_factory=list)
    conflicts: List[SourceConflict] = Field(default_factory=list)
    repetitions: List[RepeatedContent] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class ConflictListResponse(BaseModel):
    items: List[SourceConflict]
    total: int


class VerifyResultResponse(BaseModel):
    verification: VerificationResponse
    report_updated: bool = True
