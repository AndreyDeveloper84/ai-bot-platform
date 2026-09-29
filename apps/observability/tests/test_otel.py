"""OTel SDK configuration tests (DRF-705 / Sprint 8 / T1).

These tests exercise :func:`apps.observability.otel.configure_otel` in
isolation. They DO NOT touch the actual TracerProvider — `set_tracer_provider`
is a process-global one-shot in OTel's API, so the tests assert configure
behaviour by inspecting the module's own state + by patching the SDK
imports.

Sibling tests (T5 / DRF-709) drive `turn()` through ``InMemorySpanExporter``
to verify 19-span coverage and trace_id propagation end-to-end. Here we
only assert configure semantics — idempotency, no-op-on-empty-endpoint,
missing-SDK graceful path.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from opentelemetry import trace

from apps.observability import otel


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """Each test starts from a clean `_CONFIGURED=False`."""
    otel.reset_otel_for_tests()
    yield
    otel.reset_otel_for_tests()


class TestConfigureOtel:
    def test_no_endpoint_returns_noop(self, settings: Any) -> None:
        """Empty endpoint = no exporter installed, no crash, returns True."""
        settings.OTEL_EXPORTER_OTLP_ENDPOINT = ""
        assert otel.configure_otel() is True

    def test_idempotent(self, settings: Any) -> None:
        """Second call is a cheap no-op (returns False)."""
        settings.OTEL_EXPORTER_OTLP_ENDPOINT = ""
        assert otel.configure_otel() is True
        assert otel.configure_otel() is False

    def test_reset_then_reconfigure(self, settings: Any) -> None:
        settings.OTEL_EXPORTER_OTLP_ENDPOINT = ""
        otel.configure_otel()
        otel.reset_otel_for_tests()
        assert otel.configure_otel() is True

    def test_missing_sdk_does_not_crash(self, settings: Any) -> None:
        """When the OTel SDK package is unavailable, configure returns
        False AND marks the module configured to avoid retry-thrash on
        every Django request.
        """
        settings.OTEL_EXPORTER_OTLP_ENDPOINT = ""

        # Force the inner `from opentelemetry import trace` to ImportError.
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def _failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("opentelemetry"):
                raise ImportError(f"simulated missing SDK for {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_failing_import):
            result = otel.configure_otel()

        assert result is False
        assert otel._once.done is True  # don't retry on next request

    def test_endpoint_set_builds_exporter(self, settings: Any) -> None:
        """When endpoint is set, the OTLPSpanExporter constructor is called
        with ``insecure=True`` and the right URL.
        """
        settings.OTEL_EXPORTER_OTLP_ENDPOINT = "http://otel-collector.internal:4317"
        captured: dict[str, Any] = {}

        # Patch where the exporter is imported (inside _build_exporter)
        with patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter:
            mock_exporter.side_effect = lambda **kwargs: captured.update(kwargs) or object()
            otel.configure_otel()

        assert captured.get("endpoint") == "http://otel-collector.internal:4317"
        assert captured.get("insecure") is True

    def test_sample_rate_threaded_through(self, settings: Any) -> None:
        """OTEL_TRACES_SAMPLE_RATE setting propagates into the sampler.

        DRF-1697: ``TraceIdRatioBased`` is mocked here to return a bare
        float instead of a real sampler, purely so the assertion below can
        read the argument it was called with. ``configure_otel`` still
        calls ``trace.set_tracer_provider`` with a ``TracerProvider`` built
        on top of that broken sampler — and ``set_tracer_provider`` is a
        process-global one-shot in the OTel API, so letting that call
        through would leave a permanently broken provider installed for
        every other test in this pytest worker (see
        apps/observability/tests/test_sentry.py::TestScrubEvent::
        test_tags_attached_for_otel_context, which starts a real span and
        blew up with "'float' object has no attribute 'should_sample'"
        when it ran after this test with the SDK global untouched).
        `trace.set_tracer_provider` is mocked too so the real global
        provider is never touched, and the before/after comparison below
        is a belt-and-braces guard against a regression here reintroducing
        the leak.
        """
        settings.OTEL_EXPORTER_OTLP_ENDPOINT = ""
        settings.OTEL_TRACES_SAMPLE_RATE = 0.25

        provider_before = trace.get_tracer_provider()
        captured: dict[str, Any] = {}
        with (
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased") as mock_ratio,
            patch("opentelemetry.trace.set_tracer_provider") as mock_set_provider,
        ):
            mock_ratio.side_effect = lambda rate: captured.setdefault("rate", rate)
            otel.configure_otel()

        assert captured.get("rate") == 0.25
        # configure_otel() did call through to set_tracer_provider() with a
        # provider built on the mocked (broken) sampler...
        mock_set_provider.assert_called_once()
        # ...but because that call was mocked, the process-global provider
        # was never actually replaced.
        assert trace.get_tracer_provider() is provider_before
