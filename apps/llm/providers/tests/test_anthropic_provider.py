"""AnthropicProvider tests (DRF-583 / Sprint 7 / L4)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.llm.protocol import (
    CompletionResult,
    LLMError,
    LLMProvider,
    ToolCall,
)
from apps.llm.providers.anthropic_provider import (
    AnthropicProvider,
    _parse_content_blocks,
    _split_system_message,
    _to_anthropic_tool,
)


# ---------------------------------------------------------------------------
# SDK response builders
# ---------------------------------------------------------------------------


def _text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _tool_use_block(call_id: str, name: str, input_dict: dict[str, Any]) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = call_id
    block.name = name
    block.input = input_dict
    return block


def _make_response(
    *,
    content: list[Any] | None = None,
    model: str = "claude-haiku-4-5-mock",
    input_tokens: int = 10,
    output_tokens: int = 20,
    stop_reason: str = "end_turn",
) -> MagicMock:
    response = MagicMock()
    response.content = content or [_text_block("hello")]
    response.model = model
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    response.usage = usage
    response.stop_reason = stop_reason
    return response


@pytest.fixture
def patched_provider() -> tuple[AnthropicProvider, MagicMock]:
    provider = AnthropicProvider(api_key="ci-fake-key")
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock()
    provider._client = fake_client  # type: ignore[attr-defined]
    return provider, fake_client


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_implements_llmprovider(self) -> None:
        provider = AnthropicProvider(api_key="x")
        assert isinstance(provider, LLMProvider)

    def test_provider_name(self) -> None:
        assert AnthropicProvider(api_key="x").name == "anthropic"


# ---------------------------------------------------------------------------
# Embedding — raises NotImplementedError
# ---------------------------------------------------------------------------


class TestEmbeddingUnsupported:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self) -> None:
        provider = AnthropicProvider(api_key="x")
        with pytest.raises(NotImplementedError):
            await provider.embedding("часы работы")


# ---------------------------------------------------------------------------
# Complete — plain text
# ---------------------------------------------------------------------------


class TestCompletePlainText:
    @pytest.mark.asyncio
    async def test_returns_completion_result(
        self, patched_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.messages.create.return_value = _make_response(
            content=[_text_block("Привет!")],
            model="claude-sonnet-4-6",
            stop_reason="end_turn",
            input_tokens=12,
            output_tokens=3,
        )
        result = await provider.complete(
            [{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-6",
        )
        assert isinstance(result, CompletionResult)
        assert result.text == "Привет!"
        assert result.tool_calls == []
        assert result.provider == "anthropic"
        assert result.prompt_tokens == 12
        assert result.completion_tokens == 3
        assert result.finish_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_passes_temperature_and_max_tokens(
        self, patched_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.messages.create.return_value = _make_response()
        await provider.complete(
            [{"role": "user", "content": "x"}],
            temperature=0.7,
            max_tokens=256,
        )
        kw = client.messages.create.await_args.kwargs
        assert kw["temperature"] == 0.7
        assert kw["max_tokens"] == 256

    @pytest.mark.asyncio
    async def test_default_max_tokens_set_when_missing(
        self, patched_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.messages.create.return_value = _make_response()
        await provider.complete([{"role": "user", "content": "x"}])
        # Anthropic requires max_tokens; provider auto-fills 4096.
        kw = client.messages.create.await_args.kwargs
        assert kw["max_tokens"] == 4096


# ---------------------------------------------------------------------------
# System message handling — top-level kwarg
# ---------------------------------------------------------------------------


class TestSystemMessageMapping:
    def test_split_pulls_system_to_top_level(self) -> None:
        messages = [
            {"role": "system", "content": "You are a salon bot."},
            {"role": "user", "content": "hi"},
        ]
        system, rest = _split_system_message(messages)
        assert system == "You are a salon bot."
        assert rest == [{"role": "user", "content": "hi"}]

    def test_multiple_system_messages_concatenated(self) -> None:
        messages = [
            {"role": "system", "content": "part one"},
            {"role": "system", "content": "part two"},
            {"role": "user", "content": "hi"},
        ]
        system, rest = _split_system_message(messages)
        assert "part one" in system
        assert "part two" in system
        assert len(rest) == 1

    def test_no_system_message(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        system, rest = _split_system_message(messages)
        assert system == ""
        assert rest == messages

    @pytest.mark.asyncio
    async def test_system_kwarg_passed_to_sdk(
        self, patched_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.messages.create.return_value = _make_response()
        await provider.complete(
            [
                {"role": "system", "content": "You are Алина."},
                {"role": "user", "content": "hi"},
            ],
        )
        kw = client.messages.create.await_args.kwargs
        assert kw["system"] == "You are Алина."
        # messages= no longer contains the system row.
        assert all(m["role"] != "system" for m in kw["messages"])


# ---------------------------------------------------------------------------
# Tool calling
# ---------------------------------------------------------------------------


class TestToolCalling:
    def test_to_anthropic_tool_renames_parameters(self) -> None:
        spec = {
            "name": "search_kb",
            "description": "Search KB",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
        out = _to_anthropic_tool(spec)
        assert out["name"] == "search_kb"
        assert out["description"] == "Search KB"
        # Anthropic SDK calls the field input_schema, not parameters.
        assert out["input_schema"] == spec["parameters"]
        assert "parameters" not in out

    def test_parse_content_text_only(self) -> None:
        text, tool_calls = _parse_content_blocks([_text_block("hi")])
        assert text == "hi"
        assert tool_calls == []

    def test_parse_content_tool_use_block(self) -> None:
        block = _tool_use_block("call_1", "search_kb", {"query": "часы"})
        text, tool_calls = _parse_content_blocks([block])
        assert text == ""
        assert len(tool_calls) == 1
        tc = tool_calls[0]
        assert isinstance(tc, ToolCall)
        assert tc.id == "call_1"
        assert tc.name == "search_kb"
        assert tc.arguments == {"query": "часы"}

    def test_parse_mixed_text_and_tool_calls(self) -> None:
        text, tool_calls = _parse_content_blocks(
            [
                _text_block("Let me check..."),
                _tool_use_block("c1", "search_kb", {"q": "x"}),
                _text_block(" Done."),
            ]
        )
        assert text == "Let me check... Done."
        assert len(tool_calls) == 1

    @pytest.mark.asyncio
    async def test_complete_with_tools_wraps_input_schema(
        self, patched_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.messages.create.return_value = _make_response()
        spec = {
            "name": "search_kb",
            "description": "...",
            "parameters": {"type": "object"},
        }
        await provider.complete(
            [{"role": "user", "content": "x"}],
            tools=[spec],
        )
        kw = client.messages.create.await_args.kwargs
        # Tools rewritten to Anthropic shape.
        assert kw["tools"][0]["input_schema"] == spec["parameters"]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class _FakeAPIConnectionError(Exception):
    pass


_FakeAPIConnectionError.__name__ = "APIConnectionError"


class _FakeRateLimitError(Exception):
    pass


_FakeRateLimitError.__name__ = "RateLimitError"


class TestErrorMapping:
    """Non-retriable exception mapping. Retriable transients are now
    handled by the PI7 retry layer — see TestRetryAnthropic below.
    """

    @pytest.mark.asyncio
    async def test_unknown_error_becomes_llm_error(
        self, patched_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.messages.create.side_effect = ValueError("weird")
        with pytest.raises(LLMError):
            await provider.complete([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# Phase 1 / PI9 (DRF-860) — per-tenant cost cap integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTenantCostCap:
    """Anthropic provider — the per-tenant cap coexists alongside the
    pre-existing L7 per-model cap. Tenant cap is checked FIRST so
    cap-exhausted tenants see TenantQuotaExceeded (graceful fallback)
    instead of LLMProviderQuotaExceeded (router fallback)."""

    @pytest.fixture
    def tenant(self):
        from decimal import Decimal

        from apps.tenancy.models import Tenant

        return Tenant.objects.create(
            slug="anth-cost",
            name="Anthropic cost test",
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
    async def test_complete_records_usage(
        self, patched_provider: tuple[AnthropicProvider, MagicMock], tenant
    ) -> None:
        from apps.llm.cost_tracker import get_current_usage
        from apps.tenancy.context import tenant_scope

        provider, client = patched_provider
        client.messages.create.return_value = _make_response(
            input_tokens=200, output_tokens=100, model="claude-haiku-4-5"
        )

        with tenant_scope(tenant):
            await provider.complete([{"role": "user", "content": "hi"}])

        usage = await get_current_usage(str(tenant.id))
        assert usage.tokens_used == 300

    @pytest.mark.asyncio
    async def test_complete_rejected_when_tenant_cap_exhausted(
        self, patched_provider: tuple[AnthropicProvider, MagicMock], tenant
    ) -> None:
        from decimal import Decimal

        from apps.llm.cost_tracker import TenantQuotaExceeded, record_usage
        from apps.tenancy.context import tenant_scope

        await record_usage(str(tenant.id), tokens=10_000, cost_usd=Decimal("0"))

        provider, client = patched_provider
        with tenant_scope(tenant), pytest.raises(TenantQuotaExceeded):
            await provider.complete([{"role": "user", "content": "hi"}])

        client.messages.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_tenant_scope_path_still_works(
        self, patched_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.messages.create.return_value = _make_response()
        result = await provider.complete([{"role": "user", "content": "x"}])
        assert result.text == "hello"


# ---------------------------------------------------------------------------
# Phase 1 / PI7 (DRF-858) — exponential-backoff retry integration
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_retry_provider() -> tuple[AnthropicProvider, MagicMock]:
    """Provider configured with a zero-delay retry policy.

    Tests in this section assert retry BEHAVIOUR — they don't simulate
    wall-clock backoff. The backoff math has its own unit tests in
    ``apps/llm/tests/test_retry.py``.
    """
    from apps.llm.retry import RetryPolicy

    policy = RetryPolicy(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0, jitter=0.0)
    provider = AnthropicProvider(api_key="ci-fake-key", retry_policy=policy)
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock()
    provider._client = fake_client  # type: ignore[attr-defined]
    return provider, fake_client


class TestRetryAnthropic:
    """Verify retry layer wraps the Anthropic SDK call site.

    Symmetric with TestRetryOpenAI — same retry math, anthropic-
    specific predicate.
    """

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit_then_succeeds(
        self, fast_retry_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        provider, client = fast_retry_provider
        client.messages.create.side_effect = [
            _FakeRateLimitError("rate"),
            _FakeRateLimitError("still rate"),
            _make_response(content=[_text_block("finally")]),
        ]
        result = await provider.complete([{"role": "user", "content": "hi"}])
        assert result.text == "finally"
        assert client.messages.create.await_count == 3

    @pytest.mark.asyncio
    async def test_fails_fast_on_400(
        self, fast_retry_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        from apps.llm.protocol import LLMError

        provider, client = fast_retry_provider

        class _BadReq(Exception):
            pass

        _BadReq.__name__ = "BadRequestError"
        client.messages.create.side_effect = _BadReq("bad")
        with pytest.raises(LLMError):
            await provider.complete([{"role": "user", "content": "x"}])
        assert client.messages.create.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_retriable_llm_error(
        self, fast_retry_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        from apps.llm.retry import RetriableLLMError

        provider, client = fast_retry_provider
        client.messages.create.side_effect = _FakeRateLimitError("persistent")

        with pytest.raises(RetriableLLMError) as exc_info:
            await provider.complete([{"role": "user", "content": "x"}])

        assert exc_info.value.attempts == 3
        assert client.messages.create.await_count == 3

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_audit_row_per_failed_attempt(
        self, fast_retry_provider: tuple[AnthropicProvider, MagicMock]
    ) -> None:
        from asgiref.sync import sync_to_async

        from apps.audit.models import AuditLog
        from apps.llm.retry import AUDIT_RETRY_ATTEMPT_FAILED

        provider, client = fast_retry_provider
        client.messages.create.side_effect = [
            _FakeRateLimitError("first"),
            _make_response(content=[_text_block("ok")]),
        ]
        await provider.complete([{"role": "user", "content": "hi"}])

        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action=AUDIT_RETRY_ATTEMPT_FAILED))
        )()
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["provider"] == "anthropic"
        assert payload["op"] == "complete"
        assert payload["attempt"] == 1
