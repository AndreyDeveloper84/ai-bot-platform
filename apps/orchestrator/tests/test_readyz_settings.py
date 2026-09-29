"""readyz settings wiring — REDIS_URL as an attribute.

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
