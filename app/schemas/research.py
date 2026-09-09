"""Research tool request/response schemas (Phase 4)."""

from typing import List, Optional

from pydantic import BaseModel, Field


class ResearchSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)
    provider: Optional[str] = Field(
        default=None,
        description="Optional provider override (default from SEARCH_PROVIDER).",
    )


class ResearchSearchHit(BaseModel):
    title: str
    url: str
    snippet: str
    source: str


class ResearchSearchResponse(BaseModel):
    query: str
    provider: str
    results: List[ResearchSearchHit]


class ResearchFetchRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)


class ResearchFetchResponse(BaseModel):
    url: str
    final_url: str
    status_code: int
    content_type: str
    html_preview: str
    html_length: int


class ResearchExtractRequest(BaseModel):
    html: Optional[str] = Field(default=None, description="Raw HTML to extract")
    url: Optional[str] = Field(
        default=None,
        description="If provided, fetch this URL then extract",
        max_length=2048,
    )
    fallback_title: Optional[str] = None


class ResearchExtractResponse(BaseModel):
    title: str
    text: str
    word_count: int
    content_hash: str
    text_preview: str
