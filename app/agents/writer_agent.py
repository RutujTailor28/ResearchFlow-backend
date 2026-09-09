"""Writer Agent — OpenRouter-backed section/report drafting (Phase 7)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from app.core.exceptions import AiParseError, AiProviderError
from app.core.logging import get_logger
from app.providers.json_utils import extract_json_object
from app.providers.openrouter import ChatCompletionResult, OpenRouterClient, openrouter_client
from app.providers.writer_prompts import (
    create_conclusion_prompt,
    create_executive_summary_prompt,
    create_section_writer_prompt,
)

logger = get_logger(__name__)


@dataclass
class WrittenSection:
    section_id: UUID
    title: str
    markdown: str
    confidence: float = 0.5
    citation_indices: list[int] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)
    gap_notes: list[str] = field(default_factory=list)


@dataclass
class WrittenBlock:
    markdown: str
    confidence: float = 0.5


class WriterAgent:
    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self._client = client or openrouter_client

    def write_section(
        self,
        *,
        topic: str,
        audience: str,
        tone: str,
        section_id: UUID,
        section_title: str,
        section_description: str,
        sub_questions: list[str],
        findings: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        instructions: str | None = None,
        allowed_citation_indices: set[int] | None = None,
    ) -> tuple[WrittenSection, ChatCompletionResult]:
        system, user = create_section_writer_prompt(
            topic=topic,
            audience=audience,
            tone=tone,
            section_title=section_title,
            section_description=section_description,
            sub_questions=sub_questions,
            findings=findings,
            sources=sources,
            instructions=instructions,
        )
        result = self._complete(system=system, user=user, operation="write-section")
        parsed = self._parse_section_json(result.content)
        allowed = allowed_citation_indices or {
            int(s["citation_index"]) for s in sources if "citation_index" in s
        }
        markdown = self._sanitize_citations(str(parsed.get("markdown") or ""), allowed)
        indices = self._filter_indices(parsed.get("citation_indices_used"), allowed)
        # Also harvest indices actually present after sanitize.
        indices = sorted(set(indices) | set(self._extract_citation_indices(markdown)))

        confidence = float(parsed.get("confidence") or 0.5)
        confidence = max(0.0, min(1.0, confidence))
        if not findings:
            confidence = min(confidence, 0.25)

        section = WrittenSection(
            section_id=section_id,
            title=str(parsed.get("title") or section_title).strip() or section_title,
            markdown=markdown.strip(),
            confidence=confidence,
            citation_indices=indices,
            unverified_claims=[
                str(x).strip()
                for x in (parsed.get("unverified_claims") or [])
                if str(x).strip()
            ][:20],
            gap_notes=[
                str(x).strip() for x in (parsed.get("gap_notes") or []) if str(x).strip()
            ][:20],
        )
        if not findings and not section.gap_notes:
            section.gap_notes = [
                f"No research findings were available for section “{section_title}”."
            ]
        return section, result

    def write_executive_summary(
        self,
        *,
        topic: str,
        audience: str,
        tone: str,
        section_summaries: list[dict[str, Any]],
    ) -> tuple[WrittenBlock, ChatCompletionResult]:
        system, user = create_executive_summary_prompt(
            topic=topic,
            audience=audience,
            tone=tone,
            section_summaries=section_summaries,
        )
        result = self._complete(system=system, user=user, operation="write-exec-summary")
        parsed = self._parse_object(result.content)
        return (
            WrittenBlock(
                markdown=str(parsed.get("markdown") or "").strip(),
                confidence=max(0.0, min(1.0, float(parsed.get("confidence") or 0.6))),
            ),
            result,
        )

    def write_conclusion(
        self,
        *,
        topic: str,
        audience: str,
        tone: str,
        section_summaries: list[dict[str, Any]],
        gap_notes: list[str],
    ) -> tuple[WrittenBlock, ChatCompletionResult]:
        system, user = create_conclusion_prompt(
            topic=topic,
            audience=audience,
            tone=tone,
            section_summaries=section_summaries,
            gap_notes=gap_notes,
        )
        result = self._complete(system=system, user=user, operation="write-conclusion")
        parsed = self._parse_object(result.content)
        return (
            WrittenBlock(
                markdown=str(parsed.get("markdown") or "").strip(),
                confidence=max(0.0, min(1.0, float(parsed.get("confidence") or 0.6))),
            ),
            result,
        )

    def _complete(self, *, system: str, user: str, operation: str) -> ChatCompletionResult:
        try:
            return self._client.chat(
                system=system,
                user=user,
                operation=operation,
                json_mode=True,
                temperature=0.35,
                max_tokens=3500,
            )
        except AiProviderError as exc:
            http_status = (exc.detail or {}).get("http_status")
            if http_status in {400, 422}:
                logger.info("Retrying OpenRouter without json_mode operation=%s", operation)
                return self._client.chat(
                    system=system + "\n\nIMPORTANT: Reply with raw JSON only.",
                    user=user,
                    operation=operation,
                    json_mode=False,
                    temperature=0.35,
                    max_tokens=3500,
                )
            raise

    def _parse_section_json(self, content: str) -> dict[str, Any]:
        return self._parse_object(content)

    def _parse_object(self, content: str) -> dict[str, Any]:
        try:
            data = extract_json_object(content)
        except Exception as exc:  # noqa: BLE001
            raise AiParseError(
                "Writer agent returned invalid JSON",
                detail={"error": str(exc)},
            ) from exc
        if not isinstance(data, dict):
            raise AiParseError("Writer agent JSON must be an object")
        return data

    @staticmethod
    def _extract_citation_indices(markdown: str) -> list[int]:
        return [int(m) for m in re.findall(r"\[(\d+)\]", markdown)]

    @staticmethod
    def _filter_indices(raw: Any, allowed: set[int]) -> list[int]:
        if not isinstance(raw, list):
            return []
        out: list[int] = []
        for item in raw:
            try:
                n = int(item)
            except (TypeError, ValueError):
                continue
            if n in allowed:
                out.append(n)
        return sorted(set(out))

    @staticmethod
    def _sanitize_citations(markdown: str, allowed: set[int]) -> str:
        """Strip fabricated citation markers not in the allowed set."""

        def repl(match: re.Match[str]) -> str:
            n = int(match.group(1))
            return match.group(0) if n in allowed else ""

        cleaned = re.sub(r"\[(\d+)\]", repl, markdown)
        # Collapse leftover double spaces from removed citations.
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        return cleaned


writer_agent = WriterAgent()
