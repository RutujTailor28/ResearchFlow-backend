"""Supabase client unit tests (no live network required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.core.config import Settings
from app.core.supabase import check_supabase_connection, is_supabase_configured


def test_is_supabase_configured_false_when_missing() -> None:
    settings = Settings.model_construct(
        supabase_url=None,
        supabase_anon_key=None,
        supabase_service_role_key=None,
    )
    assert is_supabase_configured(settings) is False


def test_is_supabase_configured_true_with_service_key() -> None:
    settings = Settings.model_construct(
        supabase_url="https://example.supabase.co",
        supabase_anon_key=None,
        supabase_service_role_key="service-key",
    )
    assert is_supabase_configured(settings) is True


def test_check_supabase_not_configured() -> None:
    settings = Settings.model_construct(
        supabase_url=None,
        supabase_anon_key=None,
        supabase_service_role_key=None,
    )
    result = check_supabase_connection(settings)
    assert result == {
        "configured": False,
        "connected": False,
        "status": "not_configured",
    }


def test_check_supabase_connected() -> None:
    settings = Settings.model_construct(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-key",
        supabase_anon_key="anon-key",
    )
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("app.core.supabase.httpx.get", return_value=mock_response) as mocked:
        result = check_supabase_connection(settings)

    assert result["configured"] is True
    assert result["connected"] is True
    assert result["status"] == "ok"
    mocked.assert_called_once()
    assert mocked.call_args.args[0].endswith("/auth/v1/health")


def test_check_supabase_unreachable() -> None:
    settings = Settings.model_construct(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-key",
        supabase_anon_key=None,
    )
    with patch(
        "app.core.supabase.httpx.get",
        side_effect=httpx.ConnectError("boom"),
    ):
        result = check_supabase_connection(settings)

    assert result["configured"] is True
    assert result["connected"] is False
    assert result["status"] == "unreachable"
