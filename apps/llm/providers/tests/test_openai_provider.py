"""OpenAIProvider tests (DRF-581 / Sprint 7 / L2).

All openai-SDK calls are mocked. The provider is exercised purely
through its public surface (``complete`` + ``embedding``) — we never
hit the real API in CI.
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
    LLMQuotaError,
    LLMTransportError,
    ToolCall,
)
from apps.llm.providers.openai_provider import OpenAIProvider


# ---------------------------------------------------------------------------
# SDK response builders
# ---------------------------------------------------------------------------


def _make_completion_response(
    *,
    content: str = "hello",
    tool_calls: list[Any] | None = None,
    model: str = "gpt-4o-mini-mock",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    finish_reason: str = "stop",
) -> MagicMock:
    """Mimic the OpenAI SDK's ChatCompletion shape."""
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


def _make_embedding_response(vec: list[float]) -> MagicMock:
    datum = MagicMock()
    datum.embedding = vec
    response = MagicMock()
    response.data = [datum]
    return response


@pytest.fixture
def patched_provider() -> tuple[OpenAIProvider, MagicMock]:
    """Return a provider with its `_client` pre-injected by a MagicMock."""
    provider = OpenAIProvider(api_key="ci-fake-key")
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock()
    fake_client.embeddings.create = AsyncMock()
    provider._client = fake_client  # type: ignore[attr-defined]
    return provider, fake_client


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_implements_llmprovider(self) -> None:
        # runtime_checkable Protocol — duck-typed but isinstance works.
        provider = OpenAIProvider(api_key="x")
        assert isinstance(provider, LLMProvider)

    def test_provider_name(self) -> None:
        assert OpenAIProvider(api_key="x").name == "openai"


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


class TestEmbedding:
    @pytest.mark.asyncio
    async def test_returns_vector(self, patched_provider: tuple[OpenAIProvider, MagicMock]) -> None:
        provider, client = patched_provider
        client.embeddings.create.return_value = _make_embedding_response([0.1, 0.2, 0.3])
        vec = await provider.embedding("часы работы")
        assert vec == [0.1, 0.2, 0.3]
        client.embeddings.create.assert_awaited_once_with(
            model="text-embedding-3-small",
            input="часы работы",
        )

    @pytest.mark.asyncio
    async def test_custom_model_passes_through(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.embeddings.create.return_value = _make_embedding_response([1.0])
        await provider.embedding("x", model="text-embedding-3-large")
        assert client.embeddings.create.await_args.kwargs["model"] == "text-embedding-3-large"


# ---------------------------------------------------------------------------
# Complete — plain text
# ---------------------------------------------------------------------------


class TestCompletePlainText:
    @pytest.mark.asyncio
    async def test_returns_completion_result(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response(
            content="Hello!",
            model="gpt-4o-mini",
            prompt_tokens=12,
            completion_tokens=3,
            finish_reason="stop",
        )
        result = await provider.complete(
            [{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )
        assert isinstance(result, CompletionResult)
        assert result.text == "Hello!"
        assert result.tool_calls == []
        assert result.prompt_tokens == 12
        assert result.completion_tokens == 3
        assert result.provider == "openai"
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_passes_temperature_and_max_tokens(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
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
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
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
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
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
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response(
            content="",
            tool_calls=[_tool_call("x", "tool_x", "{not valid json")],
        )
        with pytest.raises(LLMError, match="malformed tool_call JSON"):
            await provider.complete([{"role": "user", "content": "x"}])

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_empty_list(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.return_value = _make_completion_response()
        result = await provider.complete([{"role": "user", "content": "hi"}])
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_tools_wrapped_into_sdk_envelope(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
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
# Exception mapping
# ---------------------------------------------------------------------------


class _FakeAPIConnectionError(Exception):
    """Simulates openai.APIConnectionError by class name."""


_FakeAPIConnectionError.__name__ = "APIConnectionError"


class _FakeRateLimitError(Exception):
    pass


_FakeRateLimitError.__name__ = "RateLimitError"


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_connection_error_becomes_transport_error(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.side_effect = _FakeAPIConnectionError("boom")
        with pytest.raises(LLMTransportError):
            await provider.complete([{"role": "user", "content": "x"}])

    @pytest.mark.asyncio
    async def test_rate_limit_becomes_quota_error(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.side_effect = _FakeRateLimitError("rate-limited")
        with pytest.raises(LLMQuotaError):
            await provider.complete([{"role": "user", "content": "x"}])

    @pytest.mark.asyncio
    async def test_unknown_error_becomes_llm_error(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.chat.completions.create.side_effect = ValueError("weird")
        with pytest.raises(LLMError):
            await provider.complete([{"role": "user", "content": "x"}])

    @pytest.mark.asyncio
    async def test_embedding_error_mapped(
        self, patched_provider: tuple[OpenAIProvider, MagicMock]
    ) -> None:
        provider, client = patched_provider
        client.embeddings.create.side_effect = _FakeAPIConnectionError("network")
        with pytest.raises(LLMTransportError):
            await provider.embedding("x")
