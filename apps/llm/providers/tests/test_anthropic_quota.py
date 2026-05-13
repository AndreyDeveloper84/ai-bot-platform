"""Anthropic daily token-cap tests (DRF-585 / Sprint 7 / L7)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from django.core.cache import cache
from freezegun import freeze_time

from apps.audit.models import AuditLog
from apps.llm.protocol import LLMProviderQuotaExceeded
from apps.llm.providers.anthropic_provider import (
    EVENT_PROVIDER_QUOTA_EXCEEDED,
    AnthropicProvider,
)


# The audit test bridges sync ORM via sync_to_async; SQLite needs
# transaction-mode so the worker thread can write.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


def _completion_response(*, input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = "Привет!"
    response = MagicMock()
    response.content = [block]
    response.model = "claude-haiku-4-5-mock"
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    response.usage = usage
    response.stop_reason = "end_turn"
    return response


@pytest.fixture
def patched_provider() -> tuple[AnthropicProvider, MagicMock]:
    provider = AnthropicProvider(api_key="ci-fake-key")
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock()
    provider._client = fake_client  # type: ignore[attr-defined]
    return provider, fake_client


# ---------------------------------------------------------------------------
# Counter increment + cap enforcement
# ---------------------------------------------------------------------------


class TestCounterIncrements:
    @pytest.mark.asyncio
    async def test_counter_grows_after_each_call(
        self,
        patched_provider: tuple[AnthropicProvider, MagicMock],
        settings: pytest.FixtureRequest,
    ) -> None:
        settings.ANTHROPIC_DAILY_TOKEN_CAP = 10_000  # type: ignore[attr-defined]
        provider, client = patched_provider
        client.messages.create.return_value = _completion_response(
            input_tokens=100, output_tokens=50
        )
        await provider.complete([{"role": "user", "content": "hi"}])

        from apps.llm.providers.anthropic_provider import _current_token_count

        assert _current_token_count() == 150


class TestCapEnforcement:
    @pytest.mark.asyncio
    async def test_at_cap_raises_provider_quota_exceeded(
        self,
        patched_provider: tuple[AnthropicProvider, MagicMock],
        settings: pytest.FixtureRequest,
    ) -> None:
        settings.ANTHROPIC_DAILY_TOKEN_CAP = 500  # type: ignore[attr-defined]
        provider, client = patched_provider
        # Pre-populate the counter at the cap so the next call's
        # pre-call gate trips.
        from apps.llm.providers.anthropic_provider import _record_token_usage

        _record_token_usage(500)

        with pytest.raises(LLMProviderQuotaExceeded):
            await provider.complete([{"role": "user", "content": "hi"}])
        # Upstream API NOT called once the gate refused.
        client.messages.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_over_cap_raises(
        self,
        patched_provider: tuple[AnthropicProvider, MagicMock],
        settings: pytest.FixtureRequest,
    ) -> None:
        settings.ANTHROPIC_DAILY_TOKEN_CAP = 100  # type: ignore[attr-defined]
        provider, _client = patched_provider
        from apps.llm.providers.anthropic_provider import _record_token_usage

        _record_token_usage(200)
        with pytest.raises(LLMProviderQuotaExceeded):
            await provider.complete([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_below_cap_proceeds(
        self,
        patched_provider: tuple[AnthropicProvider, MagicMock],
        settings: pytest.FixtureRequest,
    ) -> None:
        settings.ANTHROPIC_DAILY_TOKEN_CAP = 10_000  # type: ignore[attr-defined]
        provider, client = patched_provider
        from apps.llm.providers.anthropic_provider import _record_token_usage

        _record_token_usage(500)
        client.messages.create.return_value = _completion_response()
        result = await provider.complete([{"role": "user", "content": "hi"}])
        assert result.text == "Привет!"


# ---------------------------------------------------------------------------
# UTC midnight rollover
# ---------------------------------------------------------------------------


class TestUtcMidnightRollover:
    @pytest.mark.asyncio
    async def test_new_day_resets_counter_view(
        self,
        patched_provider: tuple[AnthropicProvider, MagicMock],
        settings: pytest.FixtureRequest,
    ) -> None:
        settings.ANTHROPIC_DAILY_TOKEN_CAP = 1_000  # type: ignore[attr-defined]
        provider, client = patched_provider
        client.messages.create.return_value = _completion_response(
            input_tokens=100, output_tokens=50
        )

        with freeze_time("2026-05-13 23:00:00", tz_offset=0):
            await provider.complete([{"role": "user", "content": "hi"}])
            from apps.llm.providers.anthropic_provider import _current_token_count

            assert _current_token_count() == 150

        # New UTC day → counter key changes → reads as 0.
        with freeze_time("2026-05-14 00:30:00", tz_offset=0):
            from apps.llm.providers.anthropic_provider import _current_token_count

            assert _current_token_count() == 0


# ---------------------------------------------------------------------------
# Audit + event on quota exceeded
# ---------------------------------------------------------------------------


class TestAuditAndEvent:
    @pytest.mark.asyncio
    async def test_audit_row_written_on_quota_exceeded(
        self,
        patched_provider: tuple[AnthropicProvider, MagicMock],
        settings: pytest.FixtureRequest,
    ) -> None:
        settings.ANTHROPIC_DAILY_TOKEN_CAP = 100  # type: ignore[attr-defined]
        provider, _client = patched_provider
        from apps.llm.providers.anthropic_provider import _record_token_usage

        _record_token_usage(200)

        try:
            await provider.complete([{"role": "user", "content": "hi"}])
        except LLMProviderQuotaExceeded:
            pass

        # Test body runs inside the asyncio loop too — Django ORM
        # queries here would also hit the no-sync-in-async guard.
        # Sync-bridge the query via sync_to_async.
        from asgiref.sync import sync_to_async

        def _fetch_row() -> Any:
            rows = AuditLog.all_tenants.filter(action=EVENT_PROVIDER_QUOTA_EXCEEDED)
            return rows.first()

        row = await sync_to_async(_fetch_row, thread_sensitive=False)()
        assert row is not None
        assert row.payload["provider"] == "anthropic"
        assert row.payload["cap"] == 100
        assert row.payload["tokens"] >= 200
