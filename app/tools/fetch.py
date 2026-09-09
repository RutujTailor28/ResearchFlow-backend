"""Page fetcher with timeout + retries."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.tools.base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TOOL_TIMEOUT_S,
    ToolValidationError,
    with_retries,
)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str
    headers: dict[str, str]


def validate_http_url(url: str) -> str:
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ToolValidationError(
            "Invalid URL. Only http/https URLs are supported.",
            detail={"url": cleaned[:200]},
        )
    if len(cleaned) > 2048:
        raise ToolValidationError("URL exceeds maximum length")
    return cleaned


def fetch_page(
    url: str,
    *,
    timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> FetchResult:
    target = validate_http_url(url)

    def _do_fetch() -> FetchResult:
        with httpx.Client(
            timeout=timeout_s,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "ResearchFlowBot/1.0 (+https://localhost; research assistant)"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        ) as client:
            response = client.get(target)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            text = response.text
            # Cap extremely large pages for memory safety.
            if len(text) > 1_500_000:
                text = text[:1_500_000]
            return FetchResult(
                url=target,
                final_url=str(response.url),
                status_code=response.status_code,
                content_type=content_type,
                html=text,
                headers={k: v for k, v in response.headers.items()},
            )

    return with_retries("fetch", _do_fetch, max_retries=max_retries)
