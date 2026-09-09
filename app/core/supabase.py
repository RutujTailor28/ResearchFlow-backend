"""Supabase client helpers (server-side only)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

import httpx
from supabase import Client, create_client

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

ClientKind = Literal["service", "anon"]


def is_supabase_configured(settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    return bool(cfg.supabase_url and (cfg.supabase_service_role_key or cfg.supabase_anon_key))


def _resolve_key(settings: Settings, kind: ClientKind) -> str:
    if kind == "service":
        key = settings.supabase_service_role_key or settings.supabase_anon_key
    else:
        key = settings.supabase_anon_key or settings.supabase_service_role_key

    if not settings.supabase_url or not key:
        raise AppError(
            "Supabase is not configured. Set SUPABASE_URL and keys in .env",
            status_code=503,
        )
    return key


@lru_cache
def get_supabase_client(kind: ClientKind = "service") -> Client:
    """
    Return a cached Supabase client.

    Use `service` for trusted backend operations (bypasses RLS).
    Use `anon` for user-scoped / RLS-respecting access later.
    Never expose the service role key to the browser.
    """
    settings = get_settings()
    key = _resolve_key(settings, kind)
    assert settings.supabase_url is not None
    logger.info("Creating Supabase %s client for %s", kind, settings.supabase_url)
    return create_client(settings.supabase_url, key)


def reset_supabase_clients() -> None:
    """Clear cached clients (used in tests / settings reload)."""
    get_supabase_client.cache_clear()


def check_supabase_connection(settings: Settings | None = None) -> dict[str, Any]:
    """
    Lightweight connectivity probe against the Supabase Auth health endpoint.

    Does not require any application tables to exist yet.
    """
    cfg = settings or get_settings()
    if not is_supabase_configured(cfg):
        return {
            "configured": False,
            "connected": False,
            "status": "not_configured",
        }

    assert cfg.supabase_url is not None
    key = cfg.supabase_service_role_key or cfg.supabase_anon_key
    assert key is not None

    url = f"{cfg.supabase_url.rstrip('/')}/auth/v1/health"
    try:
        response = httpx.get(
            url,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=8.0,
        )
        connected = response.status_code < 500
        return {
            "configured": True,
            "connected": connected,
            "status": "ok" if connected else "unreachable",
            "http_status": response.status_code,
        }
    except httpx.HTTPError as exc:
        logger.warning("Supabase connection check failed: %s", type(exc).__name__)
        return {
            "configured": True,
            "connected": False,
            "status": "unreachable",
            "error": type(exc).__name__,
        }
