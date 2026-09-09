"""Readable content extraction from HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

from app.tools.base import ToolValidationError


@dataclass
class ExtractResult:
    title: str
    text: str
    word_count: int


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._chunks: list[str] = []
        self._block_tags = {
            "p",
            "div",
            "section",
            "article",
            "li",
            "br",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }
        self._skip_tags = {"script", "style", "noscript", "svg", "iframe", "nav", "footer", "header"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        lower = tag.lower()
        if lower in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if lower == "title":
            self._in_title = True
        if lower in self._block_tags:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in self._skip_tags and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if lower == "title":
            self._in_title = False
        if lower in self._block_tags:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self._title_parts.append(text)
        else:
            self._chunks.append(text + " ")


def extract_readable_content(html: str, *, fallback_title: str = "") -> ExtractResult:
    if not html or not html.strip():
        raise ToolValidationError("No HTML content to extract")

    parser = _ReadableHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - convert parser failures
        raise ToolValidationError(
            "Failed to extract readable content",
            detail={"error": type(exc).__name__},
        ) from exc

    title = " ".join(parser._title_parts).strip() or fallback_title
    if not title:
        # Try <h1> style fallback from chunks later.
        title = fallback_title or "Untitled"

    raw = "".join(parser._chunks)
    text = re.sub(r"[ \t]+", " ", raw)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < 40:
        # Fallback: strip tags naively
        stripped = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", stripped).strip()

    if len(text) > 100_000:
        text = text[:100_000]

    words = [w for w in re.split(r"\s+", text) if w]
    if not words:
        raise ToolValidationError("Extraction produced empty text")

    return ExtractResult(title=title[:500], text=text, word_count=len(words))
