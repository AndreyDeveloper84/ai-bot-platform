"""DRF-958 — Chroma runtime config normalization smoke tests.

Pins the invariant introduced after post-merge review of #1153:

    one raw env value
    → one normalized settings value
    → identical behavior in readiness probes and the actual Chroma client.

These tests live in ``tests/smoke/`` so they run in the authoritative CI gate.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured

import config.settings.base as base_settings
from apps.kb import chromadb_client as cc
from apps.orchestrator import views as readyz_views
from apps.orchestrator.health import check_chromadb_auth


@pytest.fixture
def _restore_base_settings() -> Iterator[None]:
    """Reload base settings with the original env after each test."""
    yield
    importlib.reload(base_settings)


class TestBaseHostNormalization:
    """CHROMA_HTTP_HOST is stripped at import time."""

    @pytest.mark.parametrize(
        "raw_value,expected",
        [
            ("", ""),
            ("   ", ""),
            ("\t", ""),
            ("chromadb", "chromadb"),
            (" chromadb", "chromadb"),
            ("chromadb ", "chromadb"),
            (" chromadb ", "chromadb"),
        ],
    )
    def test_host_normalized_at_import(
        self,
        raw_value: str,
        expected: str,
        _restore_base_settings: None,
    ) -> None:
        env = {"CHROMA_HTTP_HOST": raw_value}
        with mock.patch.dict(os.environ, env, clear=False):
            reloaded = importlib.reload(base_settings)
            assert reloaded.CHROMA_HTTP_HOST == expected


class TestBaseTokenNormalization:
    """CHROMA_AUTH_TOKEN is stripped at import time."""

    @pytest.mark.parametrize(
        "raw_value,expected",
        [
            ("", ""),
            ("   ", ""),
            ("\t", ""),
            ("token", "token"),
            (" token", "token"),
            ("token ", "token"),
            (" token ", "token"),
        ],
    )
    def test_token_normalized_at_import(
        self,
        raw_value: str,
        expected: str,
        _restore_base_settings: None,
    ) -> None:
        env = {"CHROMA_AUTH_TOKEN": raw_value}
        with mock.patch.dict(os.environ, env, clear=False):
            reloaded = importlib.reload(base_settings)
            assert reloaded.CHROMA_AUTH_TOKEN == expected


@pytest.fixture
def _restore_production_module() -> Iterator[None]:
    """Reload ``config.settings.production`` cleanly after the scenario."""
    import sys

    saved = sys.modules.pop("config.settings.production", None)
    yield
    if saved is not None:
        sys.modules["config.settings.production"] = saved
    else:
        sys.modules.pop("config.settings.production", None)


def _set_required_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the non-Chroma tokens production requires to boot."""
    monkeypatch.setenv("AYLA_INTERNAL_API_TOKEN", "ayla-token-abc")  # noqa: S105
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
    monkeypatch.setenv("MYSITE_WEBHOOK_HMAC_SECRET", "hmac-secret-abc")  # noqa: S105


class TestProductionAuthGuard:
    """Whitespace-only CHROMA_AUTH_TOKEN must not satisfy the production guard."""

    def test_whitespace_token_raises_improperly_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        _set_required_production_env(monkeypatch)
        monkeypatch.setenv("CHROMA_AUTH_TOKEN", "   ")  # noqa: S105
        with pytest.raises(ImproperlyConfigured) as exc_info:
            importlib.import_module("config.settings.production")
        assert "CHROMA_AUTH_TOKEN" in str(exc_info.value)

    def test_stripped_token_boots(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        _set_required_production_env(monkeypatch)
        monkeypatch.setenv("CHROMA_AUTH_TOKEN", " token ")  # noqa: S105
        module = importlib.import_module("config.settings.production")
        assert module.CHROMA_AUTH_TOKEN == "token"  # noqa: S105


class TestProbeClientSymmetry:
    """The same normalized host value produces the same remote/embedded decision
    in ``check_chromadb_auth``, ``_ping_chromadb``, and ``_build_chromadb_client``.
    """

    @pytest.mark.parametrize(
        "raw_host,expected_host,is_remote",
        [
            ("", "", False),
            (" ", "", False),
            ("\t", "", False),
            ("chromadb", "chromadb", True),
            (" chromadb", "chromadb", True),
            ("chromadb ", "chromadb", True),
            (" chromadb ", "chromadb", True),
        ],
    )
    def test_probe_and_client_agree_on_remote_vs_embedded(
        self,
        raw_host: str,
        expected_host: str,
        is_remote: bool,
        settings: pytest.FixtureRequest,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 1. Normalize the raw env value exactly as settings import does.
        with mock.patch.dict(os.environ, {"CHROMA_HTTP_HOST": raw_host}, clear=False):
            reloaded = importlib.reload(base_settings)
            normalized_host = reloaded.CHROMA_HTTP_HOST
        assert normalized_host == expected_host

        # 2. Wire the runtime settings to the normalized value.
        settings.CHROMA_HTTP_HOST = normalized_host  # type: ignore[attr-defined]
        settings.CHROMA_HTTP_PORT = 8001  # type: ignore[attr-defined]
        settings.CHROMA_AUTH_TOKEN = "test-token"  # type: ignore[attr-defined]  # noqa: S105
        settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
        cc.reset_client_cache()

        # 3. Auth probe decision.
        with patch("httpx.head", return_value=MagicMock(status_code=200)) as mock_head:
            auth_result = check_chromadb_auth()

        assert auth_result["ok"] is True
        if is_remote:
            assert auth_result.get("detail") != "no_remote_chromadb"
            mock_head.assert_called_once()
            called_url = str(mock_head.call_args[0][0])
            assert f"http://{expected_host}:8001/api/v2/heartbeat" == called_url
        else:
            assert auth_result.get("detail") == "no_remote_chromadb"
            mock_head.assert_not_called()

        # 4. Service probe decision.
        with patch("httpx.AsyncClient") as mock_async_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=MagicMock(raise_for_status=lambda: None))
            mock_async_client_class.return_value = mock_client

            asyncio.run(readyz_views._ping_chromadb())

            if is_remote:
                mock_client.get.assert_awaited_once_with(
                    f"http://{expected_host}:8001/api/v2/heartbeat"
                )
            else:
                mock_async_client_class.assert_not_called()

        # 5. Actual client builder decision.
        with patch("chromadb.HttpClient") as mock_http_client:
            mock_http_client.return_value = object()
            built = cc._build_chromadb_client()

        if is_remote:
            mock_http_client.assert_called_once()
            _, kwargs = mock_http_client.call_args
            assert kwargs["host"] == expected_host
            assert kwargs["port"] == 8001
        else:
            mock_http_client.assert_not_called()
            # Embedded mode returns a PersistentClient rooted at BASE_DIR.
            assert built is not None

        cc.reset_client_cache()

    @pytest.mark.parametrize(
        "raw_token,expected_token",
        [
            ("", ""),
            ("   ", ""),
            ("\t", ""),
            ("token", "token"),
            (" token ", "token"),
        ],
    )
    def test_auth_token_symmetry_between_probe_and_client(
        self,
        raw_token: str,
        expected_token: str,
        settings: pytest.FixtureRequest,
        tmp_path: Path,
    ) -> None:
        """A whitespace-only token is treated as unconfigured by both the auth
        probe and the HttpClient auth-settings payload."""
        with mock.patch.dict(os.environ, {"CHROMA_AUTH_TOKEN": raw_token}, clear=False):
            reloaded = importlib.reload(base_settings)
            normalized_token = reloaded.CHROMA_AUTH_TOKEN
        assert normalized_token == expected_token

        settings.CHROMA_HTTP_HOST = "chromadb"  # type: ignore[attr-defined]
        settings.CHROMA_HTTP_PORT = 8001  # type: ignore[attr-defined]
        settings.CHROMA_AUTH_TOKEN = normalized_token  # type: ignore[attr-defined]
        settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
        cc.reset_client_cache()

        # Probe must send the normalized token, or no auth header if empty.
        with patch("httpx.head", return_value=MagicMock(status_code=200)) as mock_head:
            result = check_chromadb_auth()
        assert result["ok"] is True
        call_headers = mock_head.call_args.kwargs.get("headers", {})
        if expected_token:
            assert call_headers == {"Authorization": f"Bearer {expected_token}"}
        else:
            assert call_headers == {}

        # Client must mirror the same semantics.
        with patch("chromadb.HttpClient") as mock_http_client:
            mock_http_client.return_value = object()
            cc._build_chromadb_client()

        assert mock_http_client.called
        _, kwargs = mock_http_client.call_args
        settings_kwarg = kwargs.get("settings")
        if expected_token:
            assert settings_kwarg is not None
            assert getattr(settings_kwarg, "chroma_client_auth_credentials", "") == expected_token
        else:
            assert settings_kwarg is None

        cc.reset_client_cache()
