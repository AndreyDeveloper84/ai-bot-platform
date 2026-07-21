"""readyz settings wiring — REDIS_URL / S3_ENDPOINT_URL as attributes.

Pins the fix for the silent-localhost probe bug: the readyz probes
read ``getattr(settings, ...)``, so the urls MUST exist as settings
attributes and the probes MUST call the configured value (not the
getattr localhost default).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.conf import settings as dj_settings

from apps.orchestrator import views as readyz_views


class TestSettingsAttributes:
    def test_redis_url_attribute_present(self) -> None:
        assert hasattr(dj_settings, "REDIS_URL")

    def test_s3_endpoint_url_attribute_present(self) -> None:
        assert hasattr(dj_settings, "S3_ENDPOINT_URL")

    def test_redis_url_shape(self) -> None:
        """The attribute resolves to a redis:// URL (env or safe default)."""
        assert dj_settings.REDIS_URL.startswith("redis://")


class TestProbesHitConfiguredUrl:
    @pytest.mark.asyncio
    async def test_redis_probe_uses_configured_url(self, settings) -> None:
        sentinel = "redis://redis:6379/0"  # container value, NOT localhost
        settings.REDIS_URL = sentinel
        client = MagicMock()
        client.ping = AsyncMock()
        client.aclose = AsyncMock()
        with patch("redis.asyncio.from_url", return_value=client) as from_url:
            await readyz_views._ping_redis()
        from_url.assert_called_once_with(sentinel)
        client.ping.assert_awaited_once()
        client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_minio_probe_uses_configured_endpoint(self, settings) -> None:
        sentinel = "http://minio:9000"  # container value, NOT localhost
        settings.S3_ENDPOINT_URL = sentinel
        captured = {}

        class _FakeResponse:
            def raise_for_status(self) -> None: ...

        class _FakeClient:
            def __init__(self, **_kwargs) -> None: ...
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url: str):
                captured["url"] = url
                return _FakeResponse()

        with patch("httpx.AsyncClient", side_effect=lambda **kw: _FakeClient(**kw)):
            await readyz_views._ping_minio()
        assert captured["url"] == "http://minio:9000/minio/health/live"
