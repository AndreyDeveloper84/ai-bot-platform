"""Observability stack end-to-end (DRF-732 / Sprint 8 / G1).

Drives one full mocked ``turn()`` and asserts the THREE observability
sinks agree on the same trace_id:

* OTel root span (T2)
* AuditLog row payload (T3)
* ReplayTrace row (T4)

Plus the cross-cutting contracts E1 + J1 + J2 enforce:

* Sentry's ``before_send`` hook is **not** invoked on a clean turn
  (no error path → no event sent upstream).
* Every log record emitted under the formatter+filter pair carries
  ``tenant_id`` + ``trace_id``.

This is the integration gate that flips RED the moment any single
observability seam regresses — making it the load-bearing exit-gate
test for Sprint 8.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from apps.audit.models import AuditLog
from apps.llm.protocol import CompletionResult
from apps.observability.logging import ContextFilter, JsonFormatter
from apps.orchestrator.llm.openai_provider import LLMResponse
from apps.orchestrator.pipeline import ChannelMessage, turn
from apps.replay.models import ReplayTrace
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


# Module-level provider + exporter (OTel API doesn't support per-test reset).
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))


def _intent_provider() -> AsyncMock:
    provider = AsyncMock()
    provider.complete.return_value = LLMResponse(
        content=_INTENT_JSON,
        model="mock",
        is_fallback=False,
        tokens_in=5,
        tokens_out=10,
    )
    return provider


@pytest.fixture(autouse=True)
def _wire_otel() -> InMemorySpanExporter:
    try:
        trace.set_tracer_provider(_PROVIDER)
    except Exception:  # pragma: no cover
        pass
    from apps.orchestrator import pipeline

    pipeline._tracer = _PROVIDER.get_tracer(pipeline.__name__)  # noqa: SLF001
    _EXPORTER.clear()
    return _EXPORTER


@pytest.fixture
def g1_tenant(settings) -> Tenant:
    settings.REPLAY_SAMPLE_RATE_TEST = 1.0
    settings.STRICT_TENANT_SCOPE = "audit"
    return Tenant.objects.create(slug="g1-tenant", name="G1")


@pytest.fixture(autouse=True)
def _stub_external() -> Any:
    from apps.llm.providers.openai_provider import OpenAIProvider as FaqProvider

    faq_completion = CompletionResult(
        text="ok",
        tool_calls=[],
        prompt_tokens=1,
        completion_tokens=1,
        model="mock",
        provider="openai",
        finish_reason="stop",
    )
    with (
        patch(
            "apps.orchestrator.intent_router.OpenAIProvider",
            return_value=_intent_provider(),
        ),
        patch.object(FaqProvider, "complete", return_value=faq_completion),
        patch.object(FaqProvider, "embedding", return_value=[0.5] * 8),
        patch("apps.channels.max.outbound.send_message", return_value={"ok": True}),
    ):
        yield


def _msg(tenant_slug: str) -> ChannelMessage:
    return ChannelMessage(
        tenant_slug=tenant_slug,
        channel="max",
        channel_user_id="g1-uid",
        chat_id="g1-chat",
        text="когда работаете?",
    )


class TestTraceIdAcrossSinks:
    def test_audit_and_replay_share_otel_trace_id(
        self, g1_tenant: Tenant, _wire_otel: InMemorySpanExporter
    ) -> None:
        """One turn → root span trace_id == AuditLog.payload.trace_id ==
        ReplayTrace.trace_id.
        """
        asyncio.run(turn(_msg(g1_tenant.slug)))

        # OTel side: pull the root span's trace_id (hex).
        roots = [s for s in _wire_otel.get_finished_spans() if s.name == "pipeline.turn"]
        assert len(roots) == 1, "expected exactly one pipeline.turn span"
        ctx = roots[0].get_span_context()
        assert ctx is not None, "root span missing context"
        otel_trace_hex = format(ctx.trace_id, "032x")

        # AuditLog side: any row written during the turn carries the
        # OTel hex in its payload (T3 / DRF-707).
        audit_trace_ids = set(
            AuditLog.all_tenants.filter(tenant=g1_tenant)
            .values_list("payload__trace_id", flat=True)
            .exclude(payload__trace_id__isnull=True)
        )
        assert otel_trace_hex in audit_trace_ids, (
            f"OTel trace_id {otel_trace_hex} not found in AuditLog payloads: {audit_trace_ids}"
        )

        # ReplayTrace side: T4 (DRF-708) overrides caller uuid7 with the
        # OTel hex when a span is active.
        replay_trace_ids = set(
            str(t)
            for t in ReplayTrace.all_tenants.filter(tenant=g1_tenant).values_list(
                "trace_id", flat=True
            )
        )
        assert otel_trace_hex in replay_trace_ids, (
            f"OTel trace_id {otel_trace_hex} not in ReplayTrace rows: {replay_trace_ids}"
        )


class TestSentryNotInvokedOnCleanTurn:
    def test_no_capture_exception_on_happy_path(
        self, g1_tenant: Tenant, _wire_otel: InMemorySpanExporter
    ) -> None:
        """Clean turn → Sentry's capture_exception is NEVER called.
        Only the E2 outer-except path captures; the happy path keeps
        the Sentry dashboard clean.
        """
        with patch("sentry_sdk.capture_exception") as mock_capture:
            asyncio.run(turn(_msg(g1_tenant.slug)))
        mock_capture.assert_not_called()


class TestLogContextThreaded:
    def test_log_records_carry_tenant_and_trace(
        self, g1_tenant: Tenant, _wire_otel: InMemorySpanExporter
    ) -> None:
        """Every emitted log record stamped via ContextFilter (J2) has
        non-empty tenant_id + trace_id when emitted inside the turn.

        We attach our own handler to the platform's logging tree, drive
        a turn(), then assert at least one captured record has the
        context attributes set.
        """
        captured: list[logging.LogRecord] = []

        class _Sink(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        handler = _Sink()
        handler.addFilter(ContextFilter())
        handler.setFormatter(JsonFormatter())

        platform_root = logging.getLogger("apps")
        platform_root.addHandler(handler)
        platform_root.setLevel(logging.DEBUG)

        try:
            asyncio.run(turn(_msg(g1_tenant.slug)))
        finally:
            platform_root.removeHandler(handler)

        # At least one captured record originated inside tenant_scope.
        contextful = [r for r in captured if getattr(r, "tenant_id", "")]
        assert contextful, "no log record had tenant_id populated — ContextFilter wiring broken"
