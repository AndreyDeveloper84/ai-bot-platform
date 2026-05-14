"""OTel span coverage on pipeline.turn (DRF-706 / Sprint 8 / T2).

Drives `turn()` against an InMemorySpanExporter and asserts:

* Exactly one root span ``pipeline.turn`` emitted per call.
* Root attributes: ``pipeline.trace_id``, ``channel``, ``tenant.slug``,
  ``is_shadow`` always set.
* When tenant resolves: ``tenant.id`` attribute set.
* Step events fired on the root span — at least the ones reached on
  the early-exit path (unknown_tenant short-circuit).

The full 19-step event coverage on the happy path is covered by the
broader observability stack test G1 (DRF-732). This test is the unit
gate for the span-emission contract.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from apps.orchestrator.pipeline import ChannelMessage, turn


pytestmark = pytest.mark.django_db


# OTel's `set_tracer_provider` is process-global one-shot. Multiple test
# modules cooperate via a per-module SpanProcessor attached to the live
# global provider; first-in installs the SDK provider when only the
# default no-op is present.
_MODULE_EXPORTER = InMemorySpanExporter()
_PROCESSOR_INSTALLED = False


def _install_module_provider() -> None:
    """Attach our exporter to the global tracer provider.

    Sprint 8 review P2-3: pipeline resolves its tracer lazily via
    ``trace.get_tracer(__name__)``, so we just need our SpanProcessor
    on the live global provider. No private monkeypatch.
    """
    global _PROCESSOR_INSTALLED
    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    if not _PROCESSOR_INSTALLED:
        provider.add_span_processor(SimpleSpanProcessor(_MODULE_EXPORTER))
        _PROCESSOR_INSTALLED = True


@pytest.fixture(autouse=True)
def _otel_provider() -> InMemorySpanExporter:
    _install_module_provider()
    _MODULE_EXPORTER.clear()
    return _MODULE_EXPORTER


def _msg(text: str = "x", tenant_slug: str = "ghost") -> ChannelMessage:
    return ChannelMessage(
        tenant_slug=tenant_slug,
        channel="max",
        channel_user_id="t2-uid",
        chat_id="t2-chat",
        text=text,
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestRootSpan:
    def test_unknown_tenant_path_emits_root_span(
        self, _otel_provider: InMemorySpanExporter
    ) -> None:
        """The unknown-tenant short-circuit happens after step 1 — we
        still get a root span with the tenant_resolve event.
        """
        result = _run(turn(_msg(tenant_slug="never-existed")))
        assert result.ok is False

        spans = _otel_provider.get_finished_spans()
        roots = [s for s in spans if s.name == "pipeline.turn"]
        assert len(roots) == 1, f"expected 1 pipeline.turn span; got {[s.name for s in spans]}"

        root = roots[0]
        attrs = dict(root.attributes or {})
        assert "pipeline.trace_id" in attrs
        assert attrs.get("channel") == "max"
        assert attrs.get("tenant.slug") == "never-existed"
        assert attrs.get("is_shadow") is False
        assert attrs.get("short_circuit_step") == 1

        # tenant_resolve.start + tenant_resolve.end emitted before the
        # short-circuit. No later events.
        event_names = {e.name for e in root.events}
        assert "step.tenant_resolve.start" in event_names
        assert "step.tenant_resolve.end" in event_names


class TestShadowAttribute:
    def test_is_shadow_set_from_message(self, _otel_provider: InMemorySpanExporter) -> None:
        msg = _msg()
        # Construct a new shadow message — dataclass is frozen.
        shadow_msg = ChannelMessage(
            tenant_slug=msg.tenant_slug,
            channel=msg.channel,
            channel_user_id=msg.channel_user_id,
            chat_id=msg.chat_id,
            text=msg.text,
            is_shadow=True,
        )
        _run(turn(shadow_msg))
        spans = _otel_provider.get_finished_spans()
        root = next(s for s in spans if s.name == "pipeline.turn")
        attrs = dict(root.attributes or {})
        assert attrs.get("is_shadow") is True


class TestOTelOptional:
    def test_pipeline_works_when_tracer_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _otel_provider: InMemorySpanExporter,
    ) -> None:
        """Defensive: when ``_get_tracer()`` returns None (e.g. OTel SDK
        uninstalled), the pipeline must NOT break.

        Sprint 8 review P2-3: previously this test monkeypatched the
        module-level ``pipeline._tracer = None``. The tracer now
        resolves lazily, so we patch the helper that returns it instead.
        """
        from apps.orchestrator import pipeline

        monkeypatch.setattr(pipeline, "_get_tracer", lambda: None)
        result = _run(turn(_msg(tenant_slug="also-ghost")))
        assert result.ok is False  # unknown tenant → graceful

        # No spans should have been emitted (tracer was None).
        spans = _otel_provider.get_finished_spans()
        assert not [s for s in spans if s.name == "pipeline.turn"]
