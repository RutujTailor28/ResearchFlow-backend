"""Source management service — fetch/extract/hash/score/persist pipeline."""

from __future__ import annotations

import hashlib
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError
from app.core.logging import get_logger
from app.repositories.sources_repository import SourcesRepository, sources_repository
from app.schemas.source import (
    CreateSourceRequest,
    SourceCreateResult,
    SourceListResponse,
    SourceResponse,
)
from app.services.run_service import RunService, run_service
from app.tools.extract import extract_readable_content
from app.tools.fetch import fetch_page, validate_http_url
from app.tools.quality import score_source_quality

logger = get_logger(__name__)


class SourceService:
    """
    Auth-ready: optional user_id can enforce run ownership later.
    """

    def __init__(
        self,
        runs: RunService | None = None,
        sources: SourcesRepository | None = None,
    ) -> None:
        self._runs = runs or run_service
        self._sources = sources or sources_repository

    def list_sources(
        self,
        run_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> SourceListResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        items = self._sources.list_for_run(run.id)
        return SourceListResponse(items=items, total=len(items))

    def get_source(
        self,
        run_id: UUID,
        source_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> SourceResponse:
        run = self._authorized_run(run_id, user_id=user_id)
        return self._sources.get_for_run(run.id, source_id)

    def add_source(
        self,
        run_id: UUID,
        payload: CreateSourceRequest,
        *,
        user_id: Optional[UUID] = None,
    ) -> SourceCreateResult:
        run = self._authorized_run(run_id, user_id=user_id)
        settings = get_settings()

        url = validate_http_url(payload.url)

        # Duplicate URL check (before network work when possible).
        existing_url = self._sources.find_by_url(run.id, url)
        if existing_url is not None:
            raise ConflictError(
                "A source with this URL already exists for the run",
                detail={"source_id": str(existing_url.id), "url": url},
            )

        fetched = fetch_page(
            url,
            timeout_s=settings.research_tool_timeout_s,
            max_retries=settings.research_tool_max_retries,
        )
        # Prefer final URL after redirects for storage, still uniqueness on requested URL too.
        final_url = validate_http_url(fetched.final_url)
        if final_url != url:
            existing_final = self._sources.find_by_url(run.id, final_url)
            if existing_final is not None:
                raise ConflictError(
                    "A source with this URL already exists for the run",
                    detail={"source_id": str(existing_final.id), "url": final_url},
                )

        extracted = extract_readable_content(
            fetched.html,
            fallback_title=payload.title or "",
        )
        title = (payload.title or extracted.title or "Untitled").strip()[:500]
        content_hash = hashlib.sha256(extracted.text.encode("utf-8")).hexdigest()

        existing_hash = self._sources.find_by_content_hash(run.id, content_hash)
        if existing_hash is not None:
            raise ConflictError(
                "A source with identical content already exists for the run",
                detail={
                    "source_id": str(existing_hash.id),
                    "content_hash": content_hash,
                },
            )

        domain = urlparse(final_url).netloc.lower().removeprefix("www.")
        quality = score_source_quality(
            url=final_url,
            domain=domain,
            title=title,
            extract_text=extracted.text,
        )

        row = {
            "id": str(uuid4()),
            "run_id": str(run.id),
            "url": final_url,
            "domain": domain,
            "title": title,
            "published_at": payload.published_at.isoformat()
            if payload.published_at
            else None,
            "quality_score": quality.score,
            "content_hash": content_hash,
            "extract_text": extracted.text,
        }
        saved = self._sources.create(row)
        logger.info(
            "Saved source id=%s run_id=%s domain=%s quality=%s",
            saved.id,
            run.id,
            domain,
            quality.score,
        )
        return SourceCreateResult(
            source=saved,
            quality_reasons=quality.reasons,
            duplicate=False,
        )

    def delete_source(
        self,
        run_id: UUID,
        source_id: UUID,
        *,
        user_id: Optional[UUID] = None,
    ) -> None:
        run = self._authorized_run(run_id, user_id=user_id)
        self._sources.delete(run.id, source_id)
        logger.info("Deleted source id=%s run_id=%s", source_id, run.id)

    def _authorized_run(self, run_id: UUID, *, user_id: Optional[UUID]):
        run = self._runs.get_run(run_id)
        if user_id is not None and run.user_id is not None and run.user_id != user_id:
            raise ForbiddenError(
                "Not allowed to access this run",
                detail={"run_id": str(run_id)},
            )
        return run


source_service = SourceService()
