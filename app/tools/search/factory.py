"""Search provider factory — swap providers without changing callers."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.tools.base import DEFAULT_MAX_RETRIES, DEFAULT_TOOL_TIMEOUT_S, ToolValidationError
from app.tools.search.base import SearchProvider
from app.tools.search.duckduckgo import DuckDuckGoSearchProvider


@lru_cache
def get_search_provider(name: str | None = None) -> SearchProvider:
    settings = get_settings()
    provider_name = (name or getattr(settings, "search_provider", None) or "duckduckgo").lower()
    timeout_s = float(getattr(settings, "research_tool_timeout_s", DEFAULT_TOOL_TIMEOUT_S))
    max_retries = int(getattr(settings, "research_tool_max_retries", DEFAULT_MAX_RETRIES))

    if provider_name in {"duckduckgo", "ddg"}:
        return DuckDuckGoSearchProvider(timeout_s=timeout_s, max_retries=max_retries)

    # Future: tavily, serpapi, bing, etc.
    raise ToolValidationError(
        f"Unknown search provider '{provider_name}'",
        detail={"provider": provider_name},
    )


def reset_search_provider_cache() -> None:
    get_search_provider.cache_clear()
