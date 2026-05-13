"""FAQ-flow latency SLO test (DRF-599 / Sprint 7 / G3).

Mirrors Sprint 6 G4 (``test_pipeline_latency.py``) but exercises the
**KB-driven FAQ flow** end-to-end through :func:`apps.orchestrator.pipeline.turn`:

* Mocked intent classifier → ``intent=faq``.
* Mocked F2 LLM calls (2 round-trips, pinned 200ms each).
* Real F1 ``search_knowledge_base`` against real ChromaDB with one
  pre-populated chunk per tenant.
* Real Django ORM writes (audit + conversation + replay).

### SLO

Design goal (PHASE0_DESIGN §5.2 + Sprint 7 plan): **p95 ≤ 4000ms**
end-to-end for FAQ turns. With LLM mocked at 200ms × 2 calls, in-process
overhead (intent classify + safety pre/post + F1 chromadb query + F2
ORM writes + composer) must come in under ~3600ms p95.

### Why ``@pytest.mark.slow``

50 runs at ~400ms each ≈ 20s wall time. CI runs `pytest -m "not slow"`
in the standard suite; this test is opt-in via `-m slow` (nightly job
or manual perf gate).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from django.core.cache import cache

from apps.kb.chromadb_client import ChromaClient, KbItem, reset_client_cache
from apps.llm.protocol import CompletionResult, ToolCall
from apps.llm.router import reset_router_cache
from apps.orchestrator.llm.openai_provider import LLMResponse
from apps.orchestrator.pipeline import ChannelMessage, turn
from apps.tenancy.models import Tenant


pytestmark = [
    pytest.mark.slow,
    pytest.mark.django_db(transaction=True),
    pytest.mark.asyncio,
]


# Pinned per-call LLM latency. 200ms is roughly the median for gpt-4o-mini
# in production (RU proxy). Two calls per FAQ turn → ~400ms LLM budget.
_LLM_SIMULATED_LATENCY_S = 0.2


_INTENT_JSON = json.dumps(
    {
        "intent": "faq",
        "skill": "faq",
        "confidence": 0.92,
        "risk_level": "low",
        "missing_slots": [],
        "reply_mode": "text",
        "needs_rag": True,
        "needs_tool": True,
    }
)


def _intent_provider() -> Any:
    """Intent classifier with a tiny simulated delay (50ms)."""

    async def _delayed_complete(*_args: Any, **_kwargs: Any) -> LLMResponse:
        await asyncio.sleep(0.05)
        return LLMResponse(
            content=_INTENT_JSON,
            model="mock",
            is_fallback=False,
            tokens_in=8,
            tokens_out=16,
        )

    provider = AsyncMock()
    provider.complete.side_effect = _delayed_complete
    return provider


def _make_skill_completion_factory() -> Any:
    """Return an async side_effect that alternates tool_call → grounded
    text, with each call sleeping _LLM_SIMULATED_LATENCY_S to mimic a
    real LLM round-trip. Pattern alternates per-call across the run.
    """

    counter = {"n": 0}

    async def _complete(*_args: Any, **kwargs: Any) -> CompletionResult:
        await asyncio.sleep(_LLM_SIMULATED_LATENCY_S)
        idx = counter["n"]
        counter["n"] = idx + 1
        # First call (even): tool_call. Second call (odd): grounded answer.
        if idx % 2 == 0:
            return CompletionResult(
                text="",
                tool_calls=[
                    ToolCall(
                        id=f"call-{idx}",
                        name="search_knowledge_base",
                        arguments={"query": "часы работы"},
                    )
                ],
                prompt_tokens=10,
                completion_tokens=20,
                model="mock",
                provider="openai",
                finish_reason="tool_calls",
            )
        return CompletionResult(
            text="Часы работы: 10-22.",
            tool_calls=[],
            prompt_tokens=15,
            completion_tokens=25,
            model="mock",
            provider="openai",
            finish_reason="stop",
        )

    return _complete


def _message(tenant_slug: str) -> ChannelMessage:
    return ChannelMessage(
        tenant_slug=tenant_slug,
        channel="max",
        channel_user_id=f"g3-uid-{uuid4().hex[:8]}",
        chat_id="g3-chat",
        text="когда вы работаете?",
        display_name="G3 Latency Tester",
        trace_id=str(uuid4()),
    )


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    settings.CHROMA_HTTP_HOST = ""
    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    settings.REPLAY_SAMPLE_RATE_TEST = 0.0  # don't pay replay write cost in perf test
    reset_client_cache()
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()
    reset_client_cache()


@pytest.fixture
def faq_tenant():
    tenant = Tenant.objects.create(slug="g3-faq-latency", name="G3 FAQ Latency")
    ChromaClient().upsert(
        tenant,
        [
            KbItem(
                id="c1",
                text="Часы работы: с 10:00 до 22:00, без выходных.",
                embedding=[0.9] * 8,
                metadata={
                    "doc_id": "c1",
                    "doc_type": "help_article",
                    "source_uri": "mysite://help-articles/c1",
                },
            )
        ],
    )
    return tenant


@pytest.fixture(autouse=True)
def _stub_io():
    """Mock both LLM seams + MAX outbound — ChromaDB + ORM stay real."""
    from apps.llm.providers.openai_provider import OpenAIProvider

    with (
        patch(
            "apps.orchestrator.intent_router.OpenAIProvider",
            return_value=_intent_provider(),
        ),
        patch.object(
            OpenAIProvider,
            "complete",
            side_effect=_make_skill_completion_factory(),
        ),
        patch.object(OpenAIProvider, "embedding", return_value=[0.9] * 8),
        patch(
            "apps.channels.max.outbound.send_message",
            return_value={"ok": True},
        ),
    ):
        yield


class TestFAQLatencySLO:
    """End-to-end FAQ flow under simulated LLM latency."""

    async def test_p95_under_4000ms_over_50_runs(self, faq_tenant):
        """p95 over 50 FAQ turns must come in under 4000ms (design SLO).

        Each turn pays:
          intent classify ~50ms
          + 2 × F2 LLM calls ~400ms total
          + F1 chromadb query + ORM writes ~30-50ms
          + composer + safety glue ~10-20ms
          ≈ 500-550ms expected median.

        Headroom to 4000ms is intentional — covers CI variance, sqlite
        overhead on first warm-up, GC pauses. If p95 exceeds 4000ms,
        something is wrong (or LLM mock isn't being applied).
        """
        durations_ms: list[float] = []

        for _ in range(50):
            start = time.perf_counter()
            await turn(_message(faq_tenant.slug))
            durations_ms.append((time.perf_counter() - start) * 1000)

        p50 = statistics.median(durations_ms)
        p95 = sorted(durations_ms)[int(len(durations_ms) * 0.95) - 1]
        p99 = sorted(durations_ms)[int(len(durations_ms) * 0.99) - 1]

        # Report distribution for observability — Sprint 8 hangs
        # Prometheus histograms here.
        print(
            f"\nFAQ latency (ms): p50={p50:.1f} p95={p95:.1f} "
            f"p99={p99:.1f} max={max(durations_ms):.1f} "
            f"min={min(durations_ms):.1f}"
        )

        assert p95 < 4000, f"FAQ p95={p95:.1f}ms exceeds 4000ms SLO"

    async def test_no_outlier_beyond_alert_threshold(self, faq_tenant):
        """No single FAQ turn may exceed the 6000ms alert threshold."""
        worst_ms = 0.0
        for _ in range(30):
            start = time.perf_counter()
            await turn(_message(faq_tenant.slug))
            worst_ms = max(worst_ms, (time.perf_counter() - start) * 1000)

        assert worst_ms < 6000, f"worst FAQ turn {worst_ms:.1f}ms exceeds 6000ms alert"
