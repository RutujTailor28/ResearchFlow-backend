"""Source schemas (Phase 4)."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateSourceRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    title: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = None

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.strip()


class SourceResponse(BaseModel):
    id: UUID
    run_id: UUID
    url: str
    domain: str
    title: str
    published_at: Optional[datetime] = None
    quality_score: float
    content_hash: str
    extract_text: str
    created_at: datetime


class SourceListResponse(BaseModel):
    items: List[SourceResponse]
    total: int


class SourceCreateResult(BaseModel):
    source: SourceResponse
    quality_reasons: List[str] = Field(default_factory=list)
    duplicate: bool = False
