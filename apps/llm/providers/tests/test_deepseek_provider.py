"""DeepSeekProvider tests (DRF-280 / Sprint 8 / EPIC-P T2).

DeepSeek reaches its API via the official ``openai`` SDK with a custom
``base_url``, so the test seams are identical to OpenAIProvider tests —
mocked ``AsyncOpenAI`` client, no live network. The behaviour-level
deltas from OpenAI are:

* :meth:`embedding` raises :class:`NotImplementedError` (DeepSeek has
  no embeddings endpoint).
* ``provider`` field on :class:`CompletionResult` is ``"deepseek"``.
* Default completion model is ``deepseek-v4-flash`` (per DRF-279 spike).
* The SDK client must be instantiated with the DeepSeek ``base_url``.
* No fallback from ``DEEPSEEK_PROXY`` to ``OPENAI_PROXY`` — DeepSeek is
  reachable from RU directly; routing it through the OpenAI proxy
  would defeat the design goal.

Everything else (retry semantics, error mapping, cost-cap integration,
tool-call parsing) follows the established Sprint 7 / L-track pattern
and is verified by the parallel tests in
:mod:`apps.llm.providers.tests.test_openai_provider`.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.llm.protocol import (
    CompletionResult,
    LLMError,
    LLMProvider,
    ToolCall,
)
from apps.llm.providers.deepseek_provider import DeepSeekProvider


# ---------------------------------------------------------------------------
# SDK response builders — mimic the openai SDK ChatCompletion shape that
# DeepSeek emits verbatim (OpenAI-compatible API).
# ---------------------------------------------------------------------------


def _make_completion_response(
    *,
    content: str = "hello",
    tool_calls: list[Any] | None = None,
    model: str = "deepseek-v4-flash-mock",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    finish_reason: str = "stop",
) -> MagicMock:
    """Mimic the OpenAI-shaped ChatCompletion response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    response = MagicMock()
    response.choices = [choice]
    response.model = model
    response.usage = usage
    return response


def _tool_call(call_id: str, name: str, arguments: str) -> MagicMock:
    """Mimic a single SDK tool_call object."""
    fn = MagicMock()
    fn.name = name
    fn.arguments = arguments
    call = MagicMock()
    call.id = call_id
    call.function = fn
    return call


@pytest.fixture
def patched_provider() -> tuple[DeepSeekProvider, MagicMock]:
    """Return a provider with its ``_client`` pre-injected by a MagicMock."""
    provider = DeepSeekProvider(api_key="ci-fake-key")
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock()
    provider._client = fake_client  # type: ignore[attr-defined]
    return provider, fake_client


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_implements_llmprovider(self) -> None:
        provider = DeepSeekProvider(api_key="x")
        assert isinstance(provider, LLMProvider)

    def test_provider_name(self) -> None:
        assert DeepSeekProvider(api_key="x").name == "deepseek"

    def test_default_completion_model(self) -> None:
        # Per DRF-279 spike §1 — primary humanizer recommendation.
        assert DeepSeekProvider(api_key="x").default_completion_model == "deepseek-v4-flash"


# ---------------------------------------------------------------------------
# Embedding — NOT supported
# ---------------------------------------------------------------------------


class TestEmbedding:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self) -> None:
        provider = DeepSeekProvider(api_key="x")
        with pytest.raises(NotImplementedError, match="embeddings"):
            await provider.embedding("часы работы")

    @pytest.mark.asyncio
    async def test_does_not_touch_sdk_client(self) -> None:
        """The NotImplementedError must surface BEFORE any SDK call,
        so the L5 router fallback (DRF-587) sees a clean signal rather
        than a transport-failure shape."""
        provider = DeepSeekProvider(api_key="x")
        fake_client = MagicMock()
        fake_client.embeddings.create = AsyncMock()
        provider._client = fake_client  # type: ignore[attr-defined]

        with pytest.raises(NotImplementedError):
            await provider.embedding("anything")

        fake_client.embeddings.create.assert_not_awaited()


# ---------------------------------------------------------------------------
# Complete — plain text
# ---------------------------------------------------------------------------


class TestCompletePlainText:
    @pytest.mark.asyncio
    async def test_returns_completion_result(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response(
            content="Привет!",
            model="deepseek-v4-flash",
            prompt_tokens=12,
            completion_tokens=3,
            finish_reason="stop",
        )
        result = await provider.complete(
            [{"role": "user", "content": "привет"}],
            model="deepseek-v4-flash",
        )
        assert isinstance(result, CompletionResult)
        assert result.text == "Привет!"
        assert result.tool_calls == []
        assert result.prompt_tokens == 12
        assert result.completion_tokens == 3
        assert result.provider == "deepseek"
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_default_model_used_when_unspecified(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response()
        await provider.complete([{"role": "user", "content": "x"}])
        assert client.chat.completions.create.await_args.kwargs["model"] == "deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_passes_temperature_and_max_tokens(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response()
        await provider.complete(
            [{"role": "user", "content": "x"}],
            temperature=0.7,
            max_tokens=128,
        )
        call_kwargs = client.chat.completions.create.await_args.kwargs
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 128


# ---------------------------------------------------------------------------
# Complete — tool calling
# ---------------------------------------------------------------------------


class TestToolCalling:
    @pytest.mark.asyncio
    async def test_single_tool_call_parsed(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response(
            content="",
            tool_calls=[
                _tool_call(
                    "call_1",
                    "search_knowledge_base",
                    json.dumps({"query": "часы работы", "k": 3}),
                )
            ],
            finish_reason="tool_calls",
        )
        result = await provider.complete(
            [{"role": "user", "content": "когда работаете"}],
            tools=[
                {
                    "name": "search_knowledge_base",
                    "description": "Search KB",
                    "parameters": {"type": "object"},
                }
            ],
        )
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert isinstance(tc, ToolCall)
        assert tc.id == "call_1"
        assert tc.name == "search_knowledge_base"
        assert tc.arguments == {"query": "часы работы", "k": 3}

    @pytest.mark.asyncio
    async def test_multi_tool_calls_preserve_order(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response(
            content="",
            tool_calls=[
                _tool_call("a", "tool_a", "{}"),
                _tool_call("b", "tool_b", json.dumps({"x": 1})),
                _tool_call("c", "tool_c", "{}"),
            ],
        )
        result = await provider.complete([{"role": "user", "content": "x"}])
        assert [tc.id for tc in result.tool_calls] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_malformed_args_raises_llm_error(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response(
            content="",
            tool_calls=[_tool_call("x", "tool_x", "{not valid json")],
        )
        with pytest.raises(LLMError, match="malformed tool_call JSON"):
            await provider.complete([{"role": "user", "content": "x"}])

    @pytest.mark.asyncio
    async def test_tools_wrapped_into_sdk_envelope(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response()
        spec = {
            "name": "search_kb",
            "description": "...",
            "parameters": {"type": "object"},
        }
        await provider.complete(
            [{"role": "user", "content": "x"}],
            tools=[spec],
        )
        call_kwargs = client.chat.completions.create.await_args.kwargs
        assert call_kwargs["tools"] == [
            {"type": "function", "function": spec},
        ]


# ---------------------------------------------------------------------------
# SDK client construction — DeepSeek base URL must be threaded in
# ---------------------------------------------------------------------------


class TestClientConstruction:
    def test_uses_deepseek_base_url_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class _FakeAsyncOpenAI:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        # Patch the openai module symbol so the lazy import in
        # ``_get_client`` resolves to our fake. Module-level import,
        # so we patch the import target via sys.modules.
        import sys
        import types as _types

        fake_module = _types.ModuleType("openai")
        fake_module.AsyncOpenAI = _FakeAsyncOpenAI  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "openai", fake_module)

        provider = DeepSeekProvider(api_key="ds-test-key")
        provider._get_client()

        assert captured["api_key"] == "ds-test-key"
        assert captured["base_url"] == "https://api.deepseek.com/v1"

    def test_does_not_fall_back_to_openai_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``DEEPSEEK_PROXY`` is opt-in; we deliberately do NOT
        fall back to ``OPENAI_PROXY``. That fallback would route
        DeepSeek through the OpenAI proxy and defeat the «directly
        reachable from RU» design goal."""
        from django.conf import settings

        # Simulate an OPENAI_PROXY in settings (e.g. RU prod).
        monkeypatch.setattr(settings, "OPENAI_PROXY", "http://openai-only-proxy:8080", raising=False)
        # Explicitly ensure DEEPSEEK_PROXY is empty.
        monkeypatch.setattr(settings, "DEEPSEEK_PROXY", "", raising=False)

        captured: dict[str, Any] = {}

        class _FakeAsyncOpenAI:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        import sys
        import types as _types

        fake_module = _types.ModuleType("openai")
        fake_module.AsyncOpenAI = _FakeAsyncOpenAI  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "openai", fake_module)

        provider = DeepSeekProvider(api_key="x")
        provider._get_client()

        # No http_client kwarg means no proxy was wired — exactly what
        # we want when only OPENAI_PROXY (not DEEPSEEK_PROXY) is set.
        assert "http_client" not in captured


# ---------------------------------------------------------------------------
# Exception mapping (non-retriable path)
# ---------------------------------------------------------------------------


class _FakeRateLimitError(Exception):
    pass


_FakeRateLimitError.__name__ = "RateLimitError"


class TestErrorMapping:
    """Non-retriable exception mapping. Retriable transients are
    handled by the PI7 retry layer — see TestRetryDeepSeek below."""

    @pytest.mark.asyncio
    async def test_unknown_error_becomes_llm_error(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.side_effect = ValueError("weird")
        with pytest.raises(LLMError):
            await provider.complete([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# Phase 1 / PI9 (DRF-860) — per-tenant cost cap integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTenantCostCap:
    """DeepSeek provider must call into apps.llm.cost_tracker for the
    completion endpoint and respect TenantQuotaExceeded at the gate.
    Mirrors the OpenAI cost-cap tests."""

    @pytest.fixture
    def tenant(self):
        from decimal import Decimal

        from apps.tenancy.models import Tenant

        return Tenant.objects.create(
            slug="ds-cost",
            name="DeepSeek cost test",
            daily_token_cap=10_000,
            daily_cost_cap_usd=Decimal("5.00"),
        )

    @pytest.fixture(autouse=True)
    def _cache_clear(self):
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    @pytest.mark.asyncio
    async def test_complete_records_usage_under_tenant_scope(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock], tenant
    ) -> None:
        from apps.llm.cost_tracker import get_current_usage
        from apps.tenancy.context import tenant_scope

        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response(
            content="ответ",
            model="deepseek-v4-flash",
            prompt_tokens=100,
            completion_tokens=50,
        )

        with tenant_scope(tenant):
            await provider.complete([{"role": "user", "content": "hi"}])

        usage = await get_current_usage(str(tenant.id))
        assert usage.tokens_used == 150
        # deepseek-v4-flash: input $0.00014/1k, output $0.00028/1k.
        # 100 * 0.00014/1000 + 50 * 0.00028/1000 = 0.000014 + 0.000014 = 0.000028
        from decimal import Decimal

        assert usage.cost_used_usd == Decimal("0.000028")

    @pytest.mark.asyncio
    async def test_complete_rejected_when_cap_exhausted(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock], tenant
    ) -> None:
        from decimal import Decimal

        from apps.llm.cost_tracker import TenantQuotaExceeded, record_usage
        from apps.tenancy.context import tenant_scope

        await record_usage(str(tenant.id), tokens=10_000, cost_usd=Decimal("0"))

        provider, client = patched_provider
        with tenant_scope(tenant), pytest.raises(TenantQuotaExceeded):
            await provider.complete([{"role": "user", "content": "hi"}])

        client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_tenant_scope_skips_gate(
        self, patched_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        """Outside any tenant context the provider still works — the
        cap helper short-circuits when ``current_tenant()`` is None."""
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response()
        result = await provider.complete([{"role": "user", "content": "hi"}])
        assert result.text == "hello"


# ---------------------------------------------------------------------------
# Phase 1 / PI7 (DRF-858) — retry layer integration
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_retry_provider() -> tuple[DeepSeekProvider, MagicMock]:
    """DeepSeekProvider with zero-delay retry policy for fast tests."""
    from apps.llm.retry import RetryPolicy

    policy = RetryPolicy(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0, jitter=0.0)
    provider = DeepSeekProvider(api_key="ci-fake-key", retry_policy=policy)
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock()
    provider._client = fake_client  # type: ignore[attr-defined]
    return provider, fake_client


class TestRetryDeepSeek:
    """Verify retry layer wraps the SDK call site. DeepSeek uses the
    OpenAI Python SDK → the same ``is_retriable_openai`` predicate
    applies (transient classes are byte-identical between vendors via
    the SDK)."""

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit_then_succeeds(
        self, fast_retry_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = fast_retry_provider
        client.chat.completions.create.side_effect = [
            _FakeRateLimitError("rate"),
            _FakeRateLimitError("still rate"),
            _make_completion_response(content="finally"),
        ]
        result = await provider.complete([{"role": "user", "content": "hi"}])
        assert result.text == "finally"
        assert client.chat.completions.create.await_count == 3

    @pytest.mark.asyncio
    async def test_fails_fast_on_400(
        self, fast_retry_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        provider, client = fast_retry_provider

        class _BadReq(Exception):
            pass

        _BadReq.__name__ = "BadRequestError"
        client.chat.completions.create.side_effect = _BadReq("bad arg")
        with pytest.raises(LLMError):
            await provider.complete([{"role": "user", "content": "x"}])
        assert client.chat.completions.create.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_retriable_llm_error(
        self, fast_retry_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        from apps.llm.retry import RetriableLLMError

        provider, client = fast_retry_provider
        client.chat.completions.create.side_effect = _FakeRateLimitError("persistent")

        with pytest.raises(RetriableLLMError) as exc_info:
            await provider.complete([{"role": "user", "content": "x"}])

        assert exc_info.value.attempts == 3
        assert client.chat.completions.create.await_count == 3

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_audit_row_per_failed_attempt(
        self, fast_retry_provider: tuple[DeepSeekProvider, MagicMock]
    ) -> None:
        from apps.audit.models import AuditLog
        from apps.llm.retry import AUDIT_RETRY_ATTEMPT_FAILED

        provider, client = fast_retry_provider
        client.chat.completions.create.side_effect = [
            _FakeRateLimitError("first"),
            _make_completion_response(content="ok"),
        ]
        await provider.complete([{"role": "user", "content": "hi"}])

        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action=AUDIT_RETRY_ATTEMPT_FAILED))
        )()
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["provider"] == "deepseek"
        assert payload["op"] == "complete"
        assert payload["attempt"] == 1
        assert payload["error_class"] == "RateLimitError"
        assert payload["retriable"] is True


# sync_to_async used by the audit test above.
from asgiref.sync import sync_to_async  # noqa: E402  (test-helper, end of file)
