"""Research plan schemas."""

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.providers.prompts import get_max_sections_for_depth, get_max_subquestions_for_depth
from app.schemas.run import ResearchDepth, RunStatus


class SectionStatus(str, Enum):
    pending = "pending"
    researching = "researching"
    researched = "researched"
    writing = "writing"
    completed = "completed"
    failed = "failed"


class PlanSection(BaseModel):
    id: str
    order_index: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    sub_questions: List[str] = Field(default_factory=list)
    status: SectionStatus = SectionStatus.pending

    @field_validator("sub_questions")
    @classmethod
    def clean_sub_questions(cls, value: List[str]) -> List[str]:
        return [q.strip() for q in value if isinstance(q, str) and q.strip()]


class ResearchPlan(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    summary: str = Field(default="", max_length=5000)
    sections: List[PlanSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_ordered_unique_sections(self) -> "ResearchPlan":
        if not self.sections:
            raise ValueError("plan must include at least one section")
        seen: set[str] = set()
        for index, section in enumerate(self.sections, start=1):
            if section.id in seen:
                section.id = f"section-{index}"
            seen.add(section.id)
            section.order_index = index
        return self


class UpdatePlanRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    summary: str = Field(default="", max_length=5000)
    sections: List[PlanSection] = Field(default_factory=list)


class ImprovePlanRequest(BaseModel):
    instructions: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description='e.g. "Make the plan more technical"',
    )


class PlanMeta(BaseModel):
    generation_mode: str = "ai"
    model: Optional[str] = None
    requested_model: Optional[str] = None
    duration_ms: Optional[int] = None


class PlanDetailResponse(BaseModel):
    run: dict[str, Any]
    title: str
    summary: str
    status: RunStatus
    sections: List[PlanSection]
    meta: Optional[PlanMeta] = None


class PlanMutationResponse(BaseModel):
    plan: ResearchPlan
    run_status: RunStatus
    meta: Optional[PlanMeta] = None


def normalize_ai_plan(
    raw: Any,
    *,
    depth: ResearchDepth,
    fallback_topic: str,
) -> ResearchPlan:
    """Normalize raw AI JSON into a validated ResearchPlan."""
    if not isinstance(raw, dict):
        raise ValueError("AI plan response was not an object")

    max_sections = get_max_sections_for_depth(depth)
    max_subs = get_max_subquestions_for_depth(depth)
    sections_raw = raw.get("sections")
    if not isinstance(sections_raw, list) or not sections_raw:
        raise ValueError("AI plan response missing sections")

    sections: list[PlanSection] = []
    for index, item in enumerate(sections_raw[:max_sections], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"Section {index}").strip()
        description = str(item.get("description") or "").strip()
        sid = str(item.get("id") or f"section-{index}").strip() or f"section-{index}"

        subs_raw = item.get("sub_questions")
        if subs_raw is None:
            subs_raw = item.get("subQuestions")
        subs: list[str] = []
        if isinstance(subs_raw, list):
            for q in subs_raw:
                if isinstance(q, str) and q.strip():
                    subs.append(q.strip())
        subs = subs[:max_subs]
        if not subs:
            subs = [f"What are the key considerations for {title}?"]

        sections.append(
            PlanSection(
                id=sid,
                order_index=index,
                title=title[:300],
                description=description[:2000],
                sub_questions=subs,
                status=SectionStatus.pending,
            )
        )

    if not sections:
        raise ValueError("AI plan produced no valid sections")

    title = str(raw.get("title") or fallback_topic).strip() or fallback_topic
    summary = str(raw.get("summary") or "").strip()
    return ResearchPlan(title=title[:500], summary=summary[:5000], sections=sections)


class PlanSectionRecord(BaseModel):
    id: UUID
    run_id: UUID
    order_index: int
    title: str
    description: str
    sub_questions: List[str]
    status: SectionStatus
    created_at: datetime
    updated_at: datetime
