"""Intent router tests (DRF-536 / Sprint 6 / O2)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from apps.orchestrator.intent_router import IntentDecision, classify
from apps.orchestrator.llm.openai_provider import LLMResponse


def _provider_returning(payload: dict | str, *, is_fallback: bool = False):
    """Build a fake provider whose complete() returns a pinned LLMResponse."""

    content = payload if isinstance(payload, str) else json.dumps(payload)
    response = LLMResponse(
        content=content,
        model="gpt-4o-mini-mock",
        is_fallback=is_fallback,
        tokens_in=10,
        tokens_out=20,
    )
    provider = AsyncMock()
    provider.complete.return_value = response
    return provider


@pytest.mark.asyncio
class TestHappyPath:
    async def test_full_decision_passes_through(self):
        provider = _provider_returning(
            {
                "intent": "faq",
                "skill": "faq",
                "confidence": 0.92,
                "risk_level": "low",
                "missing_slots": [],
                "reply_mode": "text",
                "needs_rag": True,
                "needs_tool": False,
            }
        )
        decision = await classify("сколько стоит массаж?", provider=provider)
        assert decision.intent == "faq"
        assert decision.skill == "faq"
        assert decision.confidence == 0.92
        assert decision.risk_level == "low"
        assert decision.needs_rag is True
        assert decision.needs_tool is False

    async def test_handoff_intent(self):
        provider = _provider_returning(
            {
                "intent": "handoff",
                "skill": "handoff",
                "confidence": 0.85,
                "risk_level": "medium",
            }
        )
        d = await classify("оператор", provider=provider)
        assert d.intent == "handoff"
        assert d.risk_level == "medium"


@pytest.mark.asyncio
class TestValidation:
    async def test_unknown_intent_value_normalises_to_unknown(self):
        provider = _provider_returning({"intent": "bogus", "skill": "x", "confidence": 0.5})
        d = await classify("...", provider=provider)
        assert d.intent == "unknown"

    async def test_invalid_risk_level_falls_back_to_low(self):
        provider = _provider_returning(
            {"intent": "faq", "risk_level": "critical", "confidence": 0.5}
        )
        d = await classify("...", provider=provider)
        assert d.risk_level == "low"

    async def test_confidence_clamped_to_unit_range(self):
        provider = _provider_returning({"intent": "faq", "confidence": 2.5})
        d = await classify("...", provider=provider)
        assert d.confidence == 1.0

    async def test_missing_slots_coerced_to_list(self):
        provider = _provider_returning({"intent": "booking", "missing_slots": "not_a_list"})
        d = await classify("...", provider=provider)
        assert d.missing_slots == []

    async def test_skill_defaults_to_intent_when_missing(self):
        provider = _provider_returning({"intent": "faq", "confidence": 0.5})
        d = await classify("...", provider=provider)
        assert d.skill == "faq"

    async def test_raw_payload_preserved(self):
        payload = {"intent": "faq", "confidence": 0.5, "extra": {"foo": "bar"}}
        provider = _provider_returning(payload)
        d = await classify("...", provider=provider)
        assert d.raw == payload


@pytest.mark.asyncio
class TestFallback:
    async def test_breaker_open_returns_safe_fallback(self):
        provider = _provider_returning("ignored", is_fallback=True)
        d = await classify("...", provider=provider)
        assert d.intent == "unknown"
        assert d.confidence == 0.0
        assert d.risk_level == "low"

    async def test_malformed_json_returns_safe_fallback(self):
        provider = _provider_returning("not json {{{", is_fallback=False)
        d = await classify("...", provider=provider)
        assert d.intent == "unknown"

    async def test_provider_raises_returns_safe_fallback(self):
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("network down")
        d = await classify("...", provider=provider)
        assert d.intent == "unknown"


@pytest.mark.asyncio
class TestPromptBuilding:
    async def test_memory_context_injected_in_system_prompt(self):
        provider = _provider_returning({"intent": "faq", "confidence": 0.5})
        await classify(
            "...",
            memory_snapshot={
                "long_term": {
                    "rfm_segment": "champion",
                    "lifecycle_stage": "active",
                    "loyalty_tier": "gold",
                },
                "history": [{"role": "user", "content": "prev"}],
            },
            provider=provider,
        )
        # Inspect args passed to provider.complete
        args, kwargs = provider.complete.call_args
        messages = args[0]
        system_content = messages[0]["content"]
        assert "champion" in system_content
        assert "active" in system_content
        assert "gold" in system_content
        assert "recent turns: 1" in system_content

    async def test_brand_voice_forbidden_count_injected(self):
        provider = _provider_returning({"intent": "faq", "confidence": 0.5})
        await classify(
            "...",
            brand_voice={"forbidden_phrases": ["medical advice", "diagnosis"]},
            provider=provider,
        )
        args, kwargs = provider.complete.call_args
        system_content = args[0][0]["content"]
        assert "forbidden patterns active" in system_content

    async def test_response_format_pinned_to_json_object(self):
        provider = _provider_returning({"intent": "faq", "confidence": 0.5})
        await classify("...", provider=provider)
        _args, kwargs = provider.complete.call_args
        assert kwargs.get("response_format") == {"type": "json_object"}
        assert kwargs.get("max_tokens") == 200


@pytest.mark.asyncio
class TestResultShape:
    async def test_returns_intent_decision(self):
        provider = _provider_returning({"intent": "faq", "confidence": 0.5})
        d = await classify("...", provider=provider)
        assert isinstance(d, IntentDecision)
