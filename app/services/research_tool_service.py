"""Research tool orchestration service (Phase 4)."""

from __future__ import annotations

import hashlib

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.research import (
    ResearchExtractRequest,
    ResearchExtractResponse,
    ResearchFetchRequest,
    ResearchFetchResponse,
    ResearchSearchHit,
    ResearchSearchRequest,
    ResearchSearchResponse,
)
from app.tools.base import ToolValidationError
from app.tools.extract import extract_readable_content
from app.tools.fetch import fetch_page
from app.tools.search.factory import get_search_provider

logger = get_logger(__name__)


class ResearchToolService:
    def search(self, payload: ResearchSearchRequest) -> ResearchSearchResponse:
        settings = get_settings()
        provider = get_search_provider(payload.provider or settings.search_provider)
        result = provider.search(payload.query, max_results=payload.max_results)
        logger.info(
            "Research search provider=%s query_len=%s results=%s",
            result.provider,
            len(payload.query),
            len(result.results),
        )
        return ResearchSearchResponse(
            query=result.query,
            provider=result.provider,
            results=[
                ResearchSearchHit(
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    source=item.source,
                )
                for item in result.results
            ],
        )

    def fetch(self, payload: ResearchFetchRequest) -> ResearchFetchResponse:
        settings = get_settings()
        fetched = fetch_page(
            payload.url,
            timeout_s=settings.research_tool_timeout_s,
            max_retries=settings.research_tool_max_retries,
        )
        preview = fetched.html[:2000]
        logger.info(
            "Research fetch url=%s status=%s length=%s",
            fetched.final_url,
            fetched.status_code,
            len(fetched.html),
        )
        return ResearchFetchResponse(
            url=fetched.url,
            final_url=fetched.final_url,
            status_code=fetched.status_code,
            content_type=fetched.content_type,
            html_preview=preview,
            html_length=len(fetched.html),
        )

    def extract(self, payload: ResearchExtractRequest) -> ResearchExtractResponse:
        html = payload.html
        if payload.url:
            settings = get_settings()
            fetched = fetch_page(
                payload.url,
                timeout_s=settings.research_tool_timeout_s,
                max_retries=settings.research_tool_max_retries,
            )
            html = fetched.html
        if not html:
            raise ToolValidationError("Provide either html or url for extraction")

        extracted = extract_readable_content(
            html,
            fallback_title=payload.fallback_title or "",
        )
        content_hash = hashlib.sha256(extracted.text.encode("utf-8")).hexdigest()
        return ResearchExtractResponse(
            title=extracted.title,
            text=extracted.text,
            word_count=extracted.word_count,
            content_hash=content_hash,
            text_preview=extracted.text[:1000],
        )


research_tool_service = ResearchToolService()
