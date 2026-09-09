"""Planner Agent prompts."""

from __future__ import annotations

from typing import Any

from app.schemas.run import ResearchDepth

DEPTH_SECTION_LIMITS: dict[ResearchDepth, int] = {
    ResearchDepth.quick: 3,
    ResearchDepth.standard: 5,
    ResearchDepth.deep: 8,
}

DEPTH_SUBQUESTION_LIMITS: dict[ResearchDepth, int] = {
    ResearchDepth.quick: 3,
    ResearchDepth.standard: 4,
    ResearchDepth.deep: 5,
}


def get_max_sections_for_depth(depth: ResearchDepth) -> int:
    return DEPTH_SECTION_LIMITS.get(depth, 5)


def get_max_subquestions_for_depth(depth: ResearchDepth) -> int:
    return DEPTH_SUBQUESTION_LIMITS.get(depth, 4)


def create_planner_prompt(
    *,
    topic: str,
    depth: ResearchDepth,
    audience: str,
    tone: str,
    target_length: int,
    source_types: list[str],
) -> tuple[str, str]:
    max_sections = get_max_sections_for_depth(depth)
    max_subs = get_max_subquestions_for_depth(depth)

    system = f"""You are the Planner Agent for ResearchFlow, a multi-agent research report generator.
Your ONLY job is to create a structured research plan. Do NOT perform web research and do NOT write the final report.

Return ONLY valid JSON matching this schema:
{{
  "title": string,
  "summary": string,
  "sections": [
    {{
      "id": string,
      "title": string,
      "description": string,
      "sub_questions": string[]
    }}
  ]
}}

Rules:
- Create approximately {max_sections} sections for depth "{depth.value}" (at most {max_sections}).
- Prefer exactly {max_sections} high-quality sections unless the topic is extremely narrow.
- Each section needs focused sub-questions (at most {max_subs} per section).
- For depth "quick": keep coverage tight; max 3 sub-questions per section.
- For depth "standard": about 5 research focus areas / sections.
- For depth "deep": about 8 sections with deeper sub-question coverage.
- Section ids must be unique strings like "section-1".
- Keep titles concise and research-ready.
- Do not wrap the JSON in markdown fences."""

    sources = ", ".join(source_types) if source_types else "Any reputable sources"
    user = f"""Create a research plan for this brief:

Topic: {topic}
Depth: {depth.value} (max {max_sections} sections)
Audience: {audience}
Tone: {tone}
Target length: {target_length} words
Preferred sources: {sources}

Remember: plan only — no research findings yet."""

    return system, user


def create_plan_improvement_prompt(
    *,
    topic: str,
    depth: ResearchDepth,
    audience: str,
    tone: str,
    target_length: int,
    source_types: list[str],
    plan: dict[str, Any],
    instructions: str,
) -> tuple[str, str]:
    max_sections = get_max_sections_for_depth(depth)
    max_subs = get_max_subquestions_for_depth(depth)
    sources = ", ".join(source_types) if source_types else "Any reputable sources"

    system = f"""You are the Planner Agent for ResearchFlow.
Improve an existing research plan. Preserve the user's topic. Do NOT invent an unrelated plan.
Do NOT perform web research.

Return ONLY valid JSON with this schema:
{{
  "title": string,
  "summary": string,
  "sections": [
    {{
      "id": string,
      "title": string,
      "description": string,
      "sub_questions": string[]
    }}
  ]
}}

Rules:
- Keep at most {max_sections} sections for depth "{depth.value}".
- At most {max_subs} sub-questions per section.
- Preserve useful existing structure when possible.
- Apply the user's improvement instructions carefully.
- Do not wrap the JSON in markdown fences."""

    import json

    user = f"""Topic: {topic}
Depth: {depth.value}
Audience: {audience}
Tone: {tone}
Target length: {target_length} words
Preferred sources: {sources}
Improvement instructions: {instructions}

Current plan JSON:
{json.dumps(plan, indent=2)}"""

    return system, user
