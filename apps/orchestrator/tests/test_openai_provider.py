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

# transaction=True: the provider's fallback path writes its audit row via
# sync_to_async(thread_sensitive=False), which COMMITS on a separate
# connection outside the wrapping test transaction. Under plain django_db
# those rows leak into later modules (the audit_cleanup health probes in
# test_readyz_extended.py read the latest AuditLog row). Transactional
# truncation between tests keeps the leak contained.
pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


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


# ---------------------------------------------------------------------------
# DRF-1436 — the Sprint-1 provider must travel the same road as production
# ---------------------------------------------------------------------------


def _fake_completion():
    """Minimal stand-in for an OpenAI ChatCompletion response object."""

    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        model="gpt-4o-mini",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


class TestProxyAndTimeout:
    """``api.openai.com`` is unreachable directly from the Russian hosts
    this runs on. The production provider
    (:mod:`apps.llm.providers.openai_provider`) threads ``OPENAI_PROXY``
    into its ``httpx`` client and pins ``LLM_REQUEST_TIMEOUT_S``; this
    Sprint-1 wrapper shipped with neither, so any caller that still
    reaches it does not get an error — it gets an unbounded hang, which
    writes nothing anywhere. Same road, same bounds.
    """

    async def test_client_built_with_proxy_and_bounded_timeout(self, settings):
        import httpx

        settings.OPENAI_PROXY = "http://proxy.example:8080"
        settings.LLM_REQUEST_TIMEOUT_S = 30.0
        provider = OpenAIProvider(api_key="ci-fake-key")

        with (
            patch("httpx.AsyncClient") as mock_httpx,
            patch("openai.AsyncOpenAI") as mock_sdk,
        ):
            mock_sdk.return_value.chat.completions.create = AsyncMock(
                return_value=_fake_completion()
            )
            await provider._call_openai([{"role": "user", "content": "hi"}], "gpt-4o-mini")

        # The proxy is actually handed to httpx — not merely read.
        mock_httpx.assert_called_once()
        assert mock_httpx.call_args.kwargs["proxy"] == "http://proxy.example:8080"
        assert mock_httpx.call_args.kwargs["timeout"] == 30.0

        _, sdk_kwargs = mock_sdk.call_args
        assert sdk_kwargs["http_client"] is mock_httpx.return_value
        assert sdk_kwargs["timeout"] == 30.0
        # Our own retry/breaker layer owns retries; the SDK must not add
        # its own on top (DRF-989, mirrored from the production provider).
        assert sdk_kwargs["max_retries"] == 0
        assert httpx  # imported for symmetry with the production test

    async def test_no_proxy_configured_leaves_http_client_unset(self, settings):
        """No proxy in the environment must not fabricate one — CI and
        local dev reach OpenAI directly (or mock it entirely).
        """

        settings.OPENAI_PROXY = ""
        settings.LLM_REQUEST_TIMEOUT_S = 30.0
        provider = OpenAIProvider(api_key="ci-fake-key")

        with patch("openai.AsyncOpenAI") as mock_sdk:
            mock_sdk.return_value.chat.completions.create = AsyncMock(
                return_value=_fake_completion()
            )
            await provider._call_openai([{"role": "user", "content": "hi"}], "gpt-4o-mini")

        _, sdk_kwargs = mock_sdk.call_args
        assert "http_client" not in sdk_kwargs
        assert sdk_kwargs["timeout"] == 30.0


class TestFailureLeavesATrace:
    """DRF-1436 side task — an LLM refusal must not be silent.

    The breaker logs only on state *transitions*, so before this the
    first failures of an outage produced no log line at all: the owner
    learned about the outage from the alert in the chat, and the journal
    had nothing to corroborate it.
    """

    async def test_failed_call_is_logged(self, caplog):
        import logging

        provider = OpenAIProvider(api_key="ci-fake-key")

        with patch("openai.AsyncOpenAI") as mock_sdk:
            mock_sdk.return_value.chat.completions.create = AsyncMock(
                side_effect=TimeoutError("request timed out")
            )
            with caplog.at_level(logging.WARNING, logger="apps.orchestrator.llm.openai_provider"):
                with pytest.raises(TimeoutError):
                    await provider._call_openai([{"role": "user", "content": "hi"}], "gpt-4o-mini")

        assert any("openai.call_failed" in r.getMessage() for r in caplog.records)

    async def test_provider_redacts_the_proxy_before_handing_it_to_the_logger(self, settings):
        """The proxy URL carries credentials and SDK errors quote it back
        at us, userinfo included. The provider must redact it *itself*.

        Asserted on the arguments handed to the logger, NOT on
        ``caplog.records``. ``apps.observability.pii_filter``'s
        ``PIIRedactingFilter`` mutates ``record.msg`` / ``record.args`` in
        place before any handler sees them, so a
        "no secret in caplog" assertion passes whether or not this
        provider redacts anything — it would be testing the global filter
        and reporting it as coverage of this code. Verified: with
        ``redact_secrets`` removed, that formulation still passed.
        """

        # The credentials are load-bearing, not decorative — strip them and
        # the test asserts nothing. `pragma: allowlist secret` because
        # detect-secrets reads the shape, not the intent; the value is
        # invented.
        proxy_with_creds = "http://user:s3cr3t@proxy.example:8080"  # pragma: allowlist secret
        settings.OPENAI_PROXY = proxy_with_creds
        provider = OpenAIProvider(api_key="ci-fake-key")

        with (
            patch("httpx.AsyncClient"),
            patch("openai.AsyncOpenAI") as mock_sdk,
            patch("apps.orchestrator.llm.openai_provider.logger") as mock_logger,
        ):
            mock_sdk.return_value.chat.completions.create = AsyncMock(
                side_effect=RuntimeError(f"cannot connect to {proxy_with_creds}")
            )
            with pytest.raises(RuntimeError):
                await provider._call_openai([{"role": "user", "content": "hi"}], "gpt-4o-mini")

        mock_logger.warning.assert_called_once()
        fmt, *args = mock_logger.warning.call_args.args
        handed_over = " ".join(str(a) for a in args)
        assert "openai.call_failed" in fmt
        assert "s3cr3t" not in handed_over
        # Redacted, not merely truncated away: the host survives, the
        # credentials do not.
        assert "***" in handed_over
        assert "proxy.example" in handed_over
