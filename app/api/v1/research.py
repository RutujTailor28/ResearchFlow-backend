"""Standalone research tool endpoints (Phase 4)."""

from fastapi import APIRouter

from app.schemas.common import success_response
from app.schemas.research import (
    ResearchExtractRequest,
    ResearchFetchRequest,
    ResearchSearchRequest,
)
from app.services.research_tool_service import research_tool_service

router = APIRouter(prefix="/research", tags=["research"])


@router.post("/search")
def research_search(payload: ResearchSearchRequest) -> dict:
    result = research_tool_service.search(payload)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Search completed",
    )


@router.post("/fetch")
def research_fetch(payload: ResearchFetchRequest) -> dict:
    result = research_tool_service.fetch(payload)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Page fetched",
    )


@router.post("/extract")
def research_extract(payload: ResearchExtractRequest) -> dict:
    result = research_tool_service.extract(payload)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Content extracted",
    )
