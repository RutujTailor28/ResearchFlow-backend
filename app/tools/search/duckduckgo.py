"""DuckDuckGo HTML search provider (no API key required)."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.tools.base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TOOL_TIMEOUT_S,
    ToolValidationError,
    with_retries,
)
from app.tools.search.base import SearchProvider, SearchResponse, SearchResultItem


class DuckDuckGoSearchProvider:
    name = "duckduckgo"

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_retries = max_retries

    def search(self, query: str, *, max_results: int = 5) -> SearchResponse:
        cleaned = (query or "").strip()
        if len(cleaned) < 2:
            raise ToolValidationError("Search query is too short")
        limit = max(1, min(max_results, 10))

        def _do_search() -> SearchResponse:
            with httpx.Client(
                timeout=self._timeout_s,
                follow_redirects=True,
                headers={
                    "User-Agent": "ResearchFlowBot/1.0",
                    "Accept": "text/html",
                },
            ) as client:
                response = client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": cleaned},
                )
                response.raise_for_status()
                items = _parse_duckduckgo_html(response.text, limit=limit)
                return SearchResponse(
                    query=cleaned,
                    provider=self.name,
                    results=items,
                )

        return with_retries("search", _do_search, max_retries=self._max_retries)


def _parse_duckduckgo_html(html: str, *, limit: int) -> list[SearchResultItem]:
    # Result blocks typically contain result__a links and result__snippet text.
    results: list[SearchResultItem] = []
    link_pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|td|div|span)>',
        re.IGNORECASE | re.DOTALL,
    )

    links = link_pattern.findall(html)
    snippets = [_clean_html(s) for s in snippet_pattern.findall(html)]

    for index, (href, title_html) in enumerate(links):
        if len(results) >= limit:
            break
        url = _unwrap_ddg_url(unescape(href))
        if not url.startswith("http"):
            continue
        title = _clean_html(title_html) or url
        snippet = snippets[index] if index < len(snippets) else ""
        results.append(
            SearchResultItem(
                title=title[:300],
                url=url,
                snippet=snippet[:500],
                source="duckduckgo",
            )
        )
    return results


def _clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _unwrap_ddg_url(href: str) -> str:
    # DuckDuckGo often wraps destinations as /l/?uddg=<encoded>
    parsed = urlparse(href)
    if "uddg" in parse_qs(parsed.query):
        return unquote(parse_qs(parsed.query)["uddg"][0])
    if href.startswith("//"):
        return "https:" + href
    return href


# Type check compatibility with Protocol
def _assert_provider() -> SearchProvider:
    return DuckDuckGoSearchProvider()
