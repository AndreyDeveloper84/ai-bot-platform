"""Tests for the provider-agnostic LLM Protocol (DRF-580 / Sprint 7 / L1).

These tests verify the contract surface only — no provider HTTP calls.
Concrete implementations land under L2 / L4 with their own integration
tests.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from apps.llm.protocol import (
    CompletionResult,
    LLMError,
    LLMProvider,
    LLMProviderQuotaExceeded,
    LLMQuotaError,
    LLMTransportError,
    ToolCall,
)


class TestToolCall:
    def test_defaults(self) -> None:
        tc = ToolCall(id="call_abc", name="search_kb")
        assert tc.arguments == {}

    def test_args_isolated_per_instance(self) -> None:
        a = ToolCall(id="1", name="t")
        b = ToolCall(id="2", name="t")
        # default_factory must yield distinct dicts per instance.
        assert a.arguments is not b.arguments

    def test_frozen(self) -> None:
        tc = ToolCall(id="x", name="y")
        with pytest.raises(FrozenInstanceError):
            tc.name = "tampered"  # type: ignore[misc]


class TestCompletionResult:
    def test_minimal(self) -> None:
        r = CompletionResult(text="hello")
        assert r.tool_calls == []
        assert r.prompt_tokens == 0
        assert r.provider == ""

    def test_with_tool_calls(self) -> None:
        r = CompletionResult(
            text="",
            tool_calls=[ToolCall(id="c1", name="search_kb", arguments={"q": "hi"})],
            prompt_tokens=120,
            completion_tokens=5,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="tool_calls",
        )
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].arguments == {"q": "hi"}

    def test_frozen(self) -> None:
        r = CompletionResult(text="hi")
        with pytest.raises(FrozenInstanceError):
            r.text = "x"  # type: ignore[misc]


class TestExceptionHierarchy:
    """Catch-all `except LLMError` must hit every more-specific subclass.

    Call sites rely on this — the router (L5) catches
    :class:`LLMProviderQuotaExceeded` specifically for fallback, but
    everything else falls into the generic ``except LLMError`` branch.
    """

    @pytest.mark.parametrize(
        "exc",
        [LLMTransportError, LLMQuotaError, LLMProviderQuotaExceeded],
    )
    def test_subclass_of_llm_error(self, exc: type[Exception]) -> None:
        assert issubclass(exc, LLMError)

    def test_provider_quota_distinct_from_vendor_quota(self) -> None:
        # OUR daily cap and VENDOR rate-limit must be distinguishable.
        assert not issubclass(LLMQuotaError, LLMProviderQuotaExceeded)
        assert not issubclass(LLMProviderQuotaExceeded, LLMQuotaError)


class TestLLMProviderProtocol:
    """The Protocol is structural — runtime_checkable lets us isinstance()
    a duck-typed implementation without ABC inheritance.
    """

    def test_duck_typed_instance(self) -> None:
        class FakeProvider:
            name = "fake"
            # DRF-1437 widened the Protocol: a provider must publish the
            # model it defaults to, because the router's quota fallback
            # has to name a model the TARGET vendor understands.
            default_completion_model = "fake-model"
            # DRF-1443 widened it again, for the tier below the reply
            # one: the same omission on the fast tier silently sends
            # every intent classification to the reply model.
            default_fast_model = "fake-model-fast"

            async def complete(
                self,
                messages: list[dict[str, Any]],
                *,
                model: str,
                temperature: float = 0.0,
                tools: list[dict[str, Any]] | None = None,
                max_tokens: int | None = None,
            ) -> CompletionResult:
                return CompletionResult(text="ok", model=model, provider=self.name)

            async def embedding(self, text: str, *, model: str) -> list[float]:
                return [0.1, 0.2]

        # runtime_checkable Protocol — isinstance works on duck-typed
        # implementations without explicit inheritance.
        assert isinstance(FakeProvider(), LLMProvider)

    def test_missing_method_fails_isinstance(self) -> None:
        class HalfImplemented:
            name = "half"

            async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
                return CompletionResult(text="x")

            # NO embedding() — Protocol requires both.

        assert not isinstance(HalfImplemented(), LLMProvider)
