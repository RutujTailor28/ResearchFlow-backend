"""Phase 3 plan section request/response schemas."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.plan import SectionStatus


class CreateSectionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    sub_questions: List[str] = Field(default_factory=list)
    status: SectionStatus = SectionStatus.pending
    order_index: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional insert position; defaults to append at end.",
    )

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must not be blank")
        return cleaned

    @field_validator("sub_questions")
    @classmethod
    def clean_sub_questions(cls, value: List[str]) -> List[str]:
        return [q.strip() for q in value if isinstance(q, str) and q.strip()]


class UpdateSectionRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=2000)
    sub_questions: Optional[List[str]] = None
    status: Optional[SectionStatus] = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must not be blank")
        return cleaned

    @field_validator("sub_questions")
    @classmethod
    def clean_sub_questions(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value
        return [q.strip() for q in value if isinstance(q, str) and q.strip()]

    @model_validator(mode="after")
    def at_least_one_field(self) -> "UpdateSectionRequest":
        if (
            self.title is None
            and self.description is None
            and self.sub_questions is None
            and self.status is None
        ):
            raise ValueError("at least one field must be provided")
        return self


class ReorderSectionsRequest(BaseModel):
    section_ids: List[UUID] = Field(..., min_length=1)

    @model_validator(mode="after")
    def no_duplicate_ids(self) -> "ReorderSectionsRequest":
        if len(self.section_ids) != len(set(self.section_ids)):
            raise ValueError("section_ids must not contain duplicates")
        return self


class SectionResponse(BaseModel):
    id: UUID
    run_id: UUID
    order_index: int
    title: str
    description: str
    sub_questions: List[str]
    status: SectionStatus
    created_at: datetime
    updated_at: datetime


class SectionListResponse(BaseModel):
    items: List[SectionResponse]
    total: int
