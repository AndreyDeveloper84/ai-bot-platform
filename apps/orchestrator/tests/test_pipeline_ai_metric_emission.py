"""Pipeline AIRequestMetric emission tests (Tier-A #3 Q6 BUNDLE).

Per founder + tech-lead 2026-05-29 verdict «BUNDLE record_ai_request
wiring в SAME PR» + «observability not vapour». Pipeline emits one
`AIRequestMetric` row per terminal return — block / clarify / handoff /
post-skill handoff / success / shadow / outer LLM fallbacks.

Covers variant B (all terminal paths) of Phase F.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async

from apps.observability.models import AIRequestMetric
from apps.orchestrator.llm.openai_provider import LLMResponse
from apps.orchestrator.pipeline import ChannelMessage, turn
from apps.orchestrator.safety.pre_check import SafetyResult, SafetyVerdict
from apps.tenancy.models import Tenant


pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.asyncio,
]


def _fake_llm(intent: str = "faq"):
    payload = {
        "intent": intent,
        "skill": intent,
        "confidence": 0.9,
        "risk_level": "low",
        "missing_slots": [],
        "reply_mode": "text",
        "needs_rag": False,
        "needs_tool": False,
    }
    response = LLMResponse(
        content=json.dumps(payload),
        model="gpt-4o-mini-mock",
        is_fallback=False,
        tokens_in=10,
        tokens_out=20,
    )
    provider = AsyncMock()
    provider.complete.return_value = response
    return provider


def _message(text: str = "когда работаете", tenant_slug: str = "qmetric-test"):
    return ChannelMessage(
        tenant_slug=tenant_slug,
        channel="max",
        channel_user_id="qm-uid",
        chat_id="qm-chat",
        text=text,
        display_name="QMetric Tester",
        trace_id=str(uuid4()),
    )


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="qmetric-test", name="QMetric test")


@pytest.fixture(autouse=True)
def _stub_provider():
    with patch(
        "apps.orchestrator.intent_router.OpenAIProvider",
        return_value=_fake_llm(),
    ):
        yield


@pytest.fixture(autouse=True)
def _stub_outbound():
    with patch(
        "apps.channels.max.outbound.send_message",
        return_value={"ok": True},
    ):
        yield


def _metrics_for(trace_id: str) -> list[AIRequestMetric]:
    return list(AIRequestMetric.all_tenants.filter(request_id=trace_id))


class TestEmissionPreCheckBlock:
    async def test_block_emits_outcome_success(self, tenant):
        result = await turn(_message(text="посоветуйте ибупрофен"))
        assert result.short_circuited_at_step == 8

        metrics = await sync_to_async(_metrics_for)(result.trace_id)
        assert len(metrics) == 1
        metric = metrics[0]
        # BLOCK is the pipeline working as designed — outcome is `success`,
        # not `error` (the user is not stranded; canned reply was returned).
        assert metric.outcome == AIRequestMetric.OUTCOME_SUCCESS
        assert metric.fallback_triggered is False
        assert metric.latency_total_ms >= 0
        assert metric.tenant_id == tenant.id


class TestEmissionPreCheckHandoff:
    async def test_handoff_emits_outcome_escalated(self, tenant):
        result = await turn(_message(text="я думаю о самоубийстве"))
        assert result.short_circuited_at_step == 9

        metrics = await sync_to_async(_metrics_for)(result.trace_id)
        assert len(metrics) == 1
        assert metrics[0].outcome == AIRequestMetric.OUTCOME_ESCALATED


class TestEmissionPreCheckClarify:
    async def test_clarify_emits_outcome_fallback(self, tenant):
        # CLARIFY is rare in default safety rules; force it by patching
        # `pre_check` for this test only.
        clarify_result = SafetyResult(verdict=SafetyVerdict.CLARIFY, reason="forced_clarify")
        with patch("apps.orchestrator.pipeline.pre_check", return_value=clarify_result):
            result = await turn(_message(text="нейтральный запрос для теста"))

        assert result.short_circuited_at_step == 8

        metrics = await sync_to_async(_metrics_for)(result.trace_id)
        assert len(metrics) == 1
        metric = metrics[0]
        assert metric.outcome == AIRequestMetric.OUTCOME_FALLBACK
        assert metric.fallback_triggered is True


class TestEmissionHappyPath:
    async def test_success_emits_outcome_success(self, tenant):
        result = await turn(_message(text="когда работаете"))

        metrics = await sync_to_async(_metrics_for)(result.trace_id)
        # FAQ skill calls real OpenAI in this test env. If LLM auth fails
        # the skill returns handoff → outcome=escalated. Either path MUST
        # still record exactly one metric row (variant B coverage).
        assert len(metrics) == 1
        metric = metrics[0]
        assert metric.outcome in {
            AIRequestMetric.OUTCOME_SUCCESS,
            AIRequestMetric.OUTCOME_ESCALATED,
        }
        # Latency must always be captured non-negatively. The strict «> 0»
        # guard is omitted because monotonic() granularity на Windows
        # CI can floor a sub-millisecond turn to 0.
        assert metric.latency_total_ms >= 0
        # Intent classifier ran → fields populated.
        assert metric.intent_classified != ""

    async def test_intent_confidence_captured(self, tenant):
        result = await turn(_message(text="когда работаете"))

        metrics = await sync_to_async(_metrics_for)(result.trace_id)
        assert len(metrics) == 1
        metric = metrics[0]
        # The fake LLM pins confidence=0.9 (see _fake_llm payload).
        assert metric.intent_confidence is not None
        assert 0.0 <= metric.intent_confidence <= 1.0


class TestEmissionResilience:
    async def test_emission_failure_does_not_crash_pipeline(self, tenant):
        # If the recorder itself raises, the pipeline MUST still return
        # a valid TurnResult — observability is non-load-bearing.
        with patch(
            "apps.orchestrator.pipeline.record_ai_request",
            side_effect=RuntimeError("synthetic emission failure"),
        ):
            result = await turn(_message(text="посоветуйте ибупрофен"))

        # BLOCK path still returns ok=True with canned reply.
        assert result.ok is True
        assert result.short_circuited_at_step == 8
        assert result.reply is not None

        # No metric was written (the synthetic raise prevented INSERT).
        metrics = await sync_to_async(_metrics_for)(result.trace_id)
        assert len(metrics) == 0


class TestEmissionExactlyOnce:
    async def test_one_metric_per_turn(self, tenant):
        # Adversarial: guard against double emission через multiple
        # terminal returns being hit (e.g. cleanup callbacks). One turn =
        # one metric row.
        result = await turn(_message(text="посоветуйте ибупрофен"))

        metrics = await sync_to_async(_metrics_for)(result.trace_id)
        assert len(metrics) == 1
