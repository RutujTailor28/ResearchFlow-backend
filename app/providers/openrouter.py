"""OpenRouter chat completions client (server-side only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import AiConfigError, AiProviderError, AiTimeoutError
from app.core.logging import get_logger
from app.tools.base import DEFAULT_BACKOFF_BASE_S

logger = get_logger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class ChatCompletionResult:
    content: str
    requested_model: str
    model: str
    duration_ms: int
    usage: dict[str, Any] | None = None


class OpenRouterClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings_override = settings

    @property
    def settings(self) -> Settings:
        # Always read current settings (supports .env updates after restart/reload)
        return self._settings_override or get_settings()

    def chat(
        self,
        *,
        system: str,
        user: str,
        operation: str = "chat",
        temperature: float = 0.4,
        max_tokens: int = 4000,
        json_mode: bool = True,
        model: str | None = None,
    ) -> ChatCompletionResult:
        settings = self.settings
        if not settings.openrouter_api_key:
            raise AiConfigError(
                "OpenRouter is not configured. Set OPENROUTER_API_KEY in backend/.env"
            )

        primary = model or settings.get_openrouter_model()
        models = [primary, *[m for m in settings.get_openrouter_fallback_models() if m != primary]]
        max_retries = max(0, int(settings.openrouter_max_retries))

        last_error: Exception | None = None
        for candidate in models:
            for attempt in range(0, max_retries + 1):
                try:
                    return self._send_once(
                        settings=settings,
                        model=candidate,
                        system=system,
                        user=user,
                        operation=operation,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_mode=json_mode,
                    )
                except AiTimeoutError as exc:
                    last_error = exc
                    logger.warning(
                        "OpenRouter timeout operation=%s model=%s attempt=%s/%s",
                        operation,
                        candidate,
                        attempt + 1,
                        max_retries + 1,
                    )
                except AiProviderError as exc:
                    last_error = exc
                    http_status = (exc.detail or {}).get("http_status")
                    # Don't retry clear client errors other than rate-limit / transient.
                    if http_status in {400, 401, 403, 404, 422} and http_status != 429:
                        break
                    logger.warning(
                        "OpenRouter provider error operation=%s model=%s attempt=%s/%s",
                        operation,
                        candidate,
                        attempt + 1,
                        max_retries + 1,
                    )
                if attempt < max_retries:
                    import time

                    time.sleep(DEFAULT_BACKOFF_BASE_S * (2 ** attempt))
            logger.warning(
                "OpenRouter exhausting model=%s — trying fallback",
                candidate,
            )

        if isinstance(last_error, AiTimeoutError):
            raise last_error
        if isinstance(last_error, AiProviderError):
            raise last_error
        raise AiProviderError("OpenRouter request failed")

    def _send_once(
        self,
        *,
        settings: Settings,
        model: str,
        system: str,
        user: str,
        operation: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> ChatCompletionResult:
        timeout_s = max(settings.openrouter_timeout_ms / 1000.0, 5.0)
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-Title": settings.openrouter_app_title,
        }
        referer = settings.openrouter_http_referer or settings.frontend_url
        if referer:
            headers["HTTP-Referer"] = referer

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        logger.info(
            "Calling OpenRouter operation=%s requested_model=%s timeout_s=%.1f",
            operation,
            model,
            timeout_s,
        )

        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(OPENROUTER_URL, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise AiTimeoutError(detail={"operation": operation, "model": model}) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "OpenRouter transport error operation=%s type=%s",
                operation,
                type(exc).__name__,
            )
            raise AiProviderError(
                "Failed to reach OpenRouter",
                detail={"operation": operation},
            ) from exc

        if response.status_code >= 400:
            # Never log response bodies that may echo prompts/secrets.
            logger.error(
                "OpenRouter HTTP error operation=%s status=%s model=%s",
                operation,
                response.status_code,
                model,
            )
            # Some free models reject response_format — caller may retry without json_mode.
            raise AiProviderError(
                "OpenRouter request failed",
                status_code=502,
                detail={
                    "operation": operation,
                    "http_status": response.status_code,
                    "model": model,
                },
            )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise AiProviderError(
                "OpenRouter returned no choices",
                detail={"operation": operation, "model": model},
            )

        message = choices[0].get("message") or {}
        content = _extract_text(message.get("content"))
        usage = data.get("usage")
        duration_ms = int(response.elapsed.total_seconds() * 1000) if response.elapsed else 0

        preview = content[:120].replace("\n", " ")
        logger.info(
            "OpenRouter success operation=%s model=%s duration_ms=%s preview=%r",
            operation,
            data.get("model") or model,
            duration_ms,
            preview,
        )

        return ChatCompletionResult(
            content=content,
            requested_model=model,
            model=str(data.get("model") or model),
            duration_ms=duration_ms,
            usage=usage if isinstance(usage, dict) else None,
        )


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and "text" in part:
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return ""


openrouter_client = OpenRouterClient()
