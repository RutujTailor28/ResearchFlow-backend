"""Planner Agent — OpenRouter-backed research plan generation."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AiParseError, AiProviderError
from app.core.logging import get_logger
from app.providers.json_utils import extract_json_object
from app.providers.openrouter import ChatCompletionResult, OpenRouterClient, openrouter_client
from app.providers.prompts import create_plan_improvement_prompt, create_planner_prompt
from app.schemas.plan import ResearchPlan, normalize_ai_plan
from app.schemas.run import ResearchDepth, RunResponse

logger = get_logger(__name__)


class PlannerAgent:
    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self._client = client or openrouter_client

    def generate_plan(self, run: RunResponse) -> tuple[ResearchPlan, ChatCompletionResult]:
        system, user = create_planner_prompt(
            topic=run.topic,
            depth=run.depth,
            audience=str(run.audience),
            tone=str(run.tone),
            target_length=run.target_length,
            source_types=list(run.source_types),
        )
        result = self._complete(system=system, user=user, operation="plan")
        plan = self._parse_plan(result.content, depth=run.depth, topic=run.topic)
        return plan, result

    def improve_plan(
        self,
        run: RunResponse,
        plan: ResearchPlan,
        instructions: str,
    ) -> tuple[ResearchPlan, ChatCompletionResult]:
        system, user = create_plan_improvement_prompt(
            topic=run.topic,
            depth=run.depth,
            audience=str(run.audience),
            tone=str(run.tone),
            target_length=run.target_length,
            source_types=list(run.source_types),
            plan=plan.model_dump(mode="json"),
            instructions=instructions,
        )
        result = self._complete(system=system, user=user, operation="improve-plan")
        improved = self._parse_plan(result.content, depth=run.depth, topic=run.topic)
        return improved, result

    def _complete(self, *, system: str, user: str, operation: str) -> ChatCompletionResult:
        try:
            return self._client.chat(
                system=system,
                user=user,
                operation=operation,
                json_mode=True,
            )
        except AiProviderError as exc:
            # Free models sometimes reject response_format=json_object.
            http_status = (exc.detail or {}).get("http_status")
            if http_status in {400, 422}:
                logger.info("Retrying OpenRouter without json_mode operation=%s", operation)
                return self._client.chat(
                    system=system + "\n\nIMPORTANT: Reply with raw JSON only.",
                    user=user,
                    operation=operation,
                    json_mode=False,
                )
            raise

    def _parse_plan(
        self,
        content: str,
        *,
        depth: ResearchDepth,
        topic: str,
    ) -> ResearchPlan:
        try:
            raw: Any = extract_json_object(content)
            return normalize_ai_plan(raw, depth=depth, fallback_topic=topic)
        except (AiParseError, ValueError) as exc:
            logger.warning("Malformed AI plan response: %s", type(exc).__name__)
            raise AiParseError(
                "Planner AI returned invalid plan JSON",
                detail={"reason": str(exc)},
            ) from exc


planner_agent = PlannerAgent()
