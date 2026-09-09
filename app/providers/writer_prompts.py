"""Writer Agent prompts (Phase 7)."""

from __future__ import annotations

from typing import Any


def create_section_writer_prompt(
    *,
    topic: str,
    audience: str,
    tone: str,
    section_title: str,
    section_description: str,
    sub_questions: list[str],
    findings: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    instructions: str | None = None,
) -> tuple[str, str]:
    system = """You are the Writer Agent for ResearchFlow.
Write ONE report section in Markdown based only on the provided findings and sources.

Rules:
- Use ONLY the provided findings. Do not invent facts.
- Citations must use numeric markers like [1], [2] that match the provided citation indices.
- Do NOT invent citation numbers that are not in the provided sources list.
- If findings are missing or marked as gaps, acknowledge the research gap briefly.
- Paraphrase; do not copy long verbatim quotes.
- Match the requested audience and tone.
- Return ONLY valid JSON matching this schema:
{
  "title": string,
  "markdown": string,
  "confidence": number between 0 and 1,
  "citation_indices_used": number[],
  "unverified_claims": string[],
  "gap_notes": string[]
}
The markdown field should be the section body WITHOUT repeating the top-level H1 title
(use ## or paragraphs as appropriate under the section)."""

    findings_block = findings if findings else [{"note": "No findings available for this section."}]
    sources_block = sources if sources else []

    user_parts = [
        f"Topic: {topic}",
        f"Audience: {audience}",
        f"Tone: {tone}",
        f"Section title: {section_title}",
        f"Section description: {section_description or '(none)'}",
        "Sub-questions:",
        *[f"- {q}" for q in (sub_questions or ["(none)"])],
        "Findings for THIS section only (do not use other sections' findings):",
        _jsonish(findings_block),
        "Allowed sources / citation indices for THIS section:",
        _jsonish(sources_block),
    ]
    if instructions:
        user_parts.append(f"Additional instructions: {instructions}")
    user_parts.append("Write the section JSON now.")
    return system, "\n".join(user_parts)


def create_executive_summary_prompt(
    *,
    topic: str,
    audience: str,
    tone: str,
    section_summaries: list[dict[str, Any]],
) -> tuple[str, str]:
    system = """You are the Writer Agent for ResearchFlow.
Write a short executive summary in Markdown for a research report.

Rules:
- Base the summary only on the provided section summaries.
- Do not invent citations or new facts.
- Return ONLY valid JSON: {"markdown": string, "confidence": number}
"""
    user = "\n".join(
        [
            f"Topic: {topic}",
            f"Audience: {audience}",
            f"Tone: {tone}",
            "Section summaries:",
            _jsonish(section_summaries),
            "Write the executive summary JSON now.",
        ]
    )
    return system, user


def create_conclusion_prompt(
    *,
    topic: str,
    audience: str,
    tone: str,
    section_summaries: list[dict[str, Any]],
    gap_notes: list[str],
) -> tuple[str, str]:
    system = """You are the Writer Agent for ResearchFlow.
Write a concise conclusion in Markdown for a research report.

Rules:
- Synthesize only from provided section summaries.
- Mention open research gaps if provided.
- Do not invent citations.
- Return ONLY valid JSON: {"markdown": string, "confidence": number}
"""
    user = "\n".join(
        [
            f"Topic: {topic}",
            f"Audience: {audience}",
            f"Tone: {tone}",
            "Section summaries:",
            _jsonish(section_summaries),
            "Known gaps:",
            _jsonish(gap_notes or ["(none)"]),
            "Write the conclusion JSON now.",
        ]
    )
    return system, user


def _jsonish(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
