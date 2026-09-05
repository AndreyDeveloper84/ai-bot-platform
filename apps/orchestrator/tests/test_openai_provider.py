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
from apps.orchestrator.llm.openai_provider import (
    LLMOutageError,
    LLMResponse,
    OpenAIProvider,
)

# transaction=True: the provider's outage path writes its audit row via
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
    """DRF-1512 — an open breaker is a failure, not an answer.

    Until this ticket the branch below returned
    ``LLMResponse(content=<«отвечу через минуту»>, is_fallback=True)``: a
    dead upstream reaching the caller in the shape of a working one, and
    carrying the one sentence in the product that promises the bot will
    come back. Nothing in an ``LLMResponse`` can carry the flag that puts
    «Повторить» under that sentence (DRF-1489), so the promise would have
    been made with no way to keep it.

    It refuses now, and the refusal is typed and carries ``outage=True``.
    """

    async def test_refuses_with_a_typed_outage_when_breaker_open(self):
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

        # Now the breaker is open — the next call must refuse without
        # invoking _call_openai again, and must not hand back a reply.
        call_marker: list[bool] = []

        async def must_not_call(*args, **kwargs):
            call_marker.append(True)
            raise RuntimeError("should not be called")

        with patch.object(provider, "_call_openai", must_not_call):
            with pytest.raises(LLMOutageError) as excinfo:
                await provider.complete(messages=[{"role": "user", "content": "hi"}])

        assert call_marker == []  # _call_openai was not invoked
        assert excinfo.value.outage is True
        assert excinfo.value.reason == "breaker_open"
        assert excinfo.value.model == "gpt-4o-mini"
        # Still a BreakerOpenError, so a caller that already catches the
        # breaker's own refusal keeps catching this one — and so does the
        # production router's, which raises the base class.
        assert isinstance(excinfo.value, BreakerOpenError)

    async def test_outage_writes_audit(self):
        """The refusal is journalled.

        DRF-1512 renamed the action from ``llm.openai.fallback_served``:
        nothing is served any more, and the old name told anyone reading
        the journal that a person had been given a reply.
        """

        provider = OpenAIProvider(api_key="fake")
        with patch.object(
            provider,
            "_call_openai",
            AsyncMock(side_effect=RuntimeError("503")),
        ):
            for _ in range(5):
                with pytest.raises(RuntimeError):
                    await provider.complete(messages=[{"role": "user", "content": "x"}])

        # Trigger the refusal path.
        async def must_not_call(*args, **kwargs):
            raise RuntimeError("not called")

        with patch.object(provider, "_call_openai", must_not_call):
            with pytest.raises(LLMOutageError):
                await provider.complete(messages=[{"role": "user", "content": "x"}])

        # ORM reads from async test code go through sync_to_async.
        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action="llm.openai.outage_refused")),
            thread_sensitive=False,
        )()
        assert rows
        assert rows[0].payload["reason"] == "breaker_open"
        assert rows[0].payload["model"] == "gpt-4o-mini"

    async def test_breaker_open_becomes_a_typed_outage(self):
        """The plain ``BreakerOpenError`` never escapes untyped.

        Whatever the breaker itself raises, what leaves ``complete()``
        carries the ``outage`` fact — that is the whole contract the
        layer above depends on.
        """

        provider = OpenAIProvider(api_key="fake")
        # Manually patch with_circuit_breaker to raise BreakerOpenError directly.
        with patch(
            "apps.orchestrator.llm.openai_provider.with_circuit_breaker",
            AsyncMock(side_effect=BreakerOpenError("test")),
        ):
            with pytest.raises(LLMOutageError) as excinfo:
                await provider.complete(messages=[{"role": "user", "content": "x"}])

        assert excinfo.value.outage is True
        assert excinfo.value.__cause__ is not None


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
        # Presence first, and on the same string the absence is asserted
        # against: "the secret is not in there" is trivially true of an
        # empty or unrelated `handed_over`. These two say the proxy really
        # did reach the log call and was redacted rather than dropped —
        # the host survives, the credentials do not.
        assert "proxy.example" in handed_over
        assert "***" in handed_over
        assert "s3cr3t" not in handed_over
