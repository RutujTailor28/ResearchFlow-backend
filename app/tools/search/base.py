"""Search provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResultItem:
    title: str
    url: str
    snippet: str
    source: str


@dataclass
class SearchResponse:
    query: str
    provider: str
    results: list[SearchResultItem]


class SearchProvider(Protocol):
    """Replaceable web search backend."""

    name: str

    def search(self, query: str, *, max_results: int = 5) -> SearchResponse:
        ...
