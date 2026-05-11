"""OpenAIProvider tests (DRF-428 / D1).

Mocked at the ``with_circuit_breaker`` boundary so we don't make real
HTTP calls. The actual OpenAI client wrapping is verified by the
``_call_openai`` smoke that pytest-httpx covers separately when the
``ai-core`` extra is installed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import sync_to_async

from apps.audit.models import AuditLog
from apps.orchestrator.llm.breaker import BreakerOpenError, reset_breaker
from apps.orchestrator.llm.openai_provider import LLMResponse, OpenAIProvider

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def _clear_openai_breaker():
    """Reset the OpenAI breaker before each test."""

    reset_breaker("openai.complete")
    yield
    reset_breaker("openai.complete")


class TestHappyPath:
    async def test_returns_provider_response_when_breaker_closed(self):
        provider = OpenAIProvider(api_key="fake")
        expected = LLMResponse(content="hi there", model="gpt-4o-mini", is_fallback=False)
        with patch.object(provider, "_call_openai", AsyncMock(return_value=expected)):
            response = await provider.complete(messages=[{"role": "user", "content": "hi"}])
        assert response is expected
        assert response.is_fallback is False


class TestBreakerOpen:
    async def test_returns_fallback_when_breaker_open(self):
        provider = OpenAIProvider(api_key="fake")
        # Trip the breaker via 5 _call_openai failures.
        with patch.object(
            provider,
            "_call_openai",
            AsyncMock(side_effect=RuntimeError("simulated 503")),
        ):
            for _ in range(5):
                with pytest.raises(RuntimeError):
                    await provider.complete(messages=[{"role": "user", "content": "hi"}])

        # Now breaker is open — next call must return fallback without
        # invoking _call_openai again.
        call_marker: list[bool] = []

        async def must_not_call(*args, **kwargs):
            call_marker.append(True)
            raise RuntimeError("should not be called")

        with patch.object(provider, "_call_openai", must_not_call):
            response = await provider.complete(messages=[{"role": "user", "content": "hi"}])

        assert call_marker == []  # _call_openai was not invoked
        assert response.is_fallback is True
        assert "технический сбой" in response.content  # Russian fallback by default
        assert response.model == "gpt-4o-mini"

    async def test_fallback_writes_audit(self):
        provider = OpenAIProvider(api_key="fake")
        with patch.object(
            provider,
            "_call_openai",
            AsyncMock(side_effect=RuntimeError("503")),
        ):
            for _ in range(5):
                with pytest.raises(RuntimeError):
                    await provider.complete(messages=[{"role": "user", "content": "x"}])

        # Trigger the fallback path.
        async def must_not_call(*args, **kwargs):
            raise RuntimeError("not called")

        with patch.object(provider, "_call_openai", must_not_call):
            await provider.complete(messages=[{"role": "user", "content": "x"}])

        # ORM reads from async test code go through sync_to_async.
        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action="llm.openai.fallback_served")),
            thread_sensitive=False,
        )()
        assert rows
        assert rows[0].payload["reason"] == "breaker_open"

    async def test_breaker_open_raised_internally_does_not_propagate(self):
        provider = OpenAIProvider(api_key="fake")
        # Manually patch with_circuit_breaker to raise BreakerOpenError directly.
        with patch(
            "apps.orchestrator.llm.openai_provider.with_circuit_breaker",
            AsyncMock(side_effect=BreakerOpenError("test")),
        ):
            response = await provider.complete(messages=[{"role": "user", "content": "x"}])
        assert response.is_fallback is True


class TestLanguageOverride:
    async def test_english_fallback(self):
        provider = OpenAIProvider(api_key="fake", fallback_lang="en")
        with patch(
            "apps.orchestrator.llm.openai_provider.with_circuit_breaker",
            AsyncMock(side_effect=BreakerOpenError("test")),
        ):
            response = await provider.complete(messages=[{"role": "user", "content": "x"}])
        assert "brief technical issue" in response.content
        assert response.is_fallback is True
