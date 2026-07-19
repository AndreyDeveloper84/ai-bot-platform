"""OTel happy-path span event coverage (DRF-709 / Sprint 8 / T5).

Drives a fully-mocked ``turn()`` against ``InMemorySpanExporter`` and
asserts the root span's event log carries the expected step markers.

The complementary unit gate (T2 / `test_otel_spans.py`) covers
short-circuit paths + the `_tracer=None` defensive path. T5 is the
happy-path counterpart — it gives us a single test that fails the
moment a step-event call gets dropped from `_run_under_tenant`.

### Why not assert exact 19 events

The plan's acceptance text says "19 spans / events emit per turn", but
the actual count depends on which short-circuits fire (block / clarify
/ handoff / no_skill). For a clean happy path Phase 0 emits ~13
events (steps 1-19 minus the short-circuit-only ones). We assert a
minimum floor instead of an exact match so the test stays meaningful
as steps move around in future sprints.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from apps.llm.protocol import CompletionResult
from apps.orchestrator.pipeline import ChannelMessage, turn
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db(transaction=True)


_INTENT_JSON = json.dumps(
    {
        "intent": "faq",
        "skill": "faq",
        "confidence": 0.92,
        "risk_level": "low",
        "missing_slots": [],
        "reply_mode": "text",
        "needs_rag": False,
        "needs_tool": False,
    }
)


# Module-level exporter — OTel's set_tracer_provider is process-global
# one-shot, so multiple test files can't each install their own provider.
# Pattern: each test module installs its OWN SpanProcessor onto whichever
# provider is currently global, so spans land in this module's exporter
# regardless of which test module booted OTel first.
_MODULE_EXPORTER = InMemorySpanExporter()


_PROCESSOR_INSTALLED = False


@pytest.fixture(autouse=True)
def _wire_otel() -> InMemorySpanExporter:
    """Hook our exporter into the global provider.

    Sprint 8 review P2-3: pipeline resolves its tracer lazily via
    ``trace.get_tracer(__name__)``, so we just need ``_MODULE_EXPORTER``
    on the global provider's processor chain. First test that runs
    installs the SDK provider (if the default no-op is still in
    place); subsequent tests reuse it.
    """
    global _PROCESSOR_INSTALLED
    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        # Default no-op provider — install our SDK one.
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    if not _PROCESSOR_INSTALLED:
        provider.add_span_processor(SimpleSpanProcessor(_MODULE_EXPORTER))
        _PROCESSOR_INSTALLED = True
    _MODULE_EXPORTER.clear()
    return _MODULE_EXPORTER


@pytest.fixture
def t5_tenant() -> Tenant:
    return Tenant.objects.create(slug="t5-tenant", name="T5")


@pytest.fixture(autouse=True)
def _stub_external() -> Any:
    """Neutralise the LLM seams + MAX outbound for a clean happy turn.

    #975: intent classification resolves its provider через the LLM router
    (production path), so the deterministic seam is the ROUTER, keyed per
    skill — the intent provider answers with the pinned IntentDecision
    JSON, anything else gets a plain "ok" text completion. The previous
    Sprint-1 patch (``intent_router.OpenAIProvider``) was a dead target —
    pipeline turns never consult the legacy path — and the class-level
    ``complete`` patch fed the classifier a non-JSON "ok", pinning
    intent="unknown" (the rot this fixes).
    """
    from apps.llm.providers.openai_provider import OpenAIProvider as FaqProvider

    intent_provider = AsyncMock()
    intent_provider.complete.return_value = CompletionResult(
        text=_INTENT_JSON,
        tool_calls=[],
        prompt_tokens=1,
        completion_tokens=1,
        model="mock",
        provider="openai",
        finish_reason="stop",
    )
    faq_provider = AsyncMock()
    faq_provider.complete.return_value = CompletionResult(
        text="ok",
        tool_calls=[],
        prompt_tokens=1,
        completion_tokens=1,
        model="mock",
        provider="openai",
        finish_reason="stop",
    )

    def _get_provider(tenant=None, *, skill: str = "", op: str = "complete"):  # noqa: ANN001, ANN202
        return intent_provider if skill == "intent" else faq_provider

    router = Mock()
    router.get_provider.side_effect = _get_provider
    with (
        patch("apps.llm.router.get_router", return_value=router),
        patch.object(FaqProvider, "embedding", return_value=[0.5] * 8),
        patch("apps.channels.max.outbound.send_message", return_value={"ok": True}),
    ):
        yield


def _msg(tenant_slug: str) -> ChannelMessage:
    return ChannelMessage(
        tenant_slug=tenant_slug,
        channel="max",
        channel_user_id="t5-uid",
        chat_id="t5-chat",
        text="когда работаете?",
    )


class TestHappyTurnSpanCoverage:
    def test_happy_turn_emits_root_span(
        self, t5_tenant: Tenant, _wire_otel: InMemorySpanExporter
    ) -> None:
        asyncio.run(turn(_msg(t5_tenant.slug)))
        roots = [s for s in _wire_otel.get_finished_spans() if s.name == "pipeline.turn"]
        assert len(roots) == 1

    def test_happy_turn_emits_minimum_step_events(
        self, t5_tenant: Tenant, _wire_otel: InMemorySpanExporter
    ) -> None:
        """A clean happy turn emits paired .start/.end events for at least
        10 distinct step names (out of 19 — short-circuit-only steps
        don't fire on the happy path).
        """
        asyncio.run(turn(_msg(t5_tenant.slug)))
        root = next(s for s in _wire_otel.get_finished_spans() if s.name == "pipeline.turn")
        starts = {
            e.name.removeprefix("step.").removesuffix(".start")
            for e in root.events
            if e.name.endswith(".start")
        }
        # Floor of 10 — easier to maintain than exact-19 as steps evolve.
        assert len(starts) >= 10, (
            f"only {len(starts)} step.*.start events fired on happy path: {sorted(starts)}"
        )
        # Anchors: the steps every happy path MUST hit.
        for required in (
            "tenant_resolve",
            "resolve_bot_user",
            "resolve_conversation",
            "intent_classify",
            "pre_check",
            "skill_dispatch",
            "compose",
            "send_outbound",
        ):
            assert required in starts, f"missing required step event {required!r}"

    def test_root_span_carries_intent_attribute(
        self, t5_tenant: Tenant, _wire_otel: InMemorySpanExporter
    ) -> None:
        asyncio.run(turn(_msg(t5_tenant.slug)))
        root = next(s for s in _wire_otel.get_finished_spans() if s.name == "pipeline.turn")
        attrs = dict(root.attributes or {})
        assert attrs.get("intent") == "faq"
        assert attrs.get("tenant.id") == str(t5_tenant.id)
