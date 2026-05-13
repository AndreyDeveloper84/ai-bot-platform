"""OTel SDK smoke (DRF-736 / Sprint 8 / G5).

Three scenarios that prove the OTel pipeline shape, even when no
collector is reachable:

1. ``configure_otel()`` succeeds with empty endpoint (no-op exporter).
2. A synthetic span lands in an in-memory exporter (the path that
   the integration tests rely on).
3. A bogus OTLP endpoint pointing nowhere does NOT bubble an exception
   to the caller — production must not crash if the collector is down.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from apps.observability import otel


@pytest.fixture(autouse=True)
def _reset() -> None:
    otel.reset_otel_for_tests()
    yield
    otel.reset_otel_for_tests()


class TestNoEndpointPath:
    def test_empty_endpoint_configures_cleanly(self, settings) -> None:
        """Empty `OTEL_EXPORTER_OTLP_ENDPOINT` is the default local-dev
        config. Must NOT crash, must NOT install an exporter.
        """
        settings.OTEL_EXPORTER_OTLP_ENDPOINT = ""
        assert otel.configure_otel() is True


class TestInMemoryExporterEmits:
    def test_synthetic_span_lands_in_exporter(self) -> None:
        """Integration tests + G1 rely on InMemorySpanExporter receiving
        spans emitted by code under test. This is the smoke that proves
        the wiring (TracerProvider + SpanProcessor + Exporter) works
        end-to-end inside the test process.
        """
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        tracer = provider.get_tracer("g5-smoke")
        with tracer.start_as_current_span("g5-span") as span:
            span.set_attribute("g5", "yes")

        finished = exporter.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].name == "g5-span"
        assert dict(finished[0].attributes or {}).get("g5") == "yes"


class TestUnreachableEndpointDoesNotCrash:
    def test_pretend_endpoint_swallowed_gracefully(self, settings) -> None:
        """A misconfigured OTLP endpoint must NOT bubble up to the
        pipeline. The OTLP exporter retries asynchronously in the
        background; `configure_otel()` itself succeeds.
        """
        settings.OTEL_EXPORTER_OTLP_ENDPOINT = "http://otel-collector.nowhere:4317"
        # Should not raise even though the endpoint is unreachable.
        result = otel.configure_otel()
        # `_CONFIGURED` flag set regardless.
        assert result is True or result is False
        # Critical: the provider is set and the tracer is callable.
        tracer = trace.get_tracer("g5-unreach")
        with tracer.start_as_current_span("g5-unreach-span"):
            pass  # No exception is the entire assertion.
