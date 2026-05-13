"""OpenTelemetry SDK configuration (DRF-705 / Sprint 8 / T1).

Single entry point :func:`configure_otel` that:

1. Builds a ``TracerProvider`` with ``service.name`` + ``service.version`` +
   ``deployment.environment`` resource attributes.
2. Wires the OTLP/gRPC span exporter when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is
   set. Empty endpoint = no-op exporter (local dev + tests).
3. Applies a ``ParentBased(TraceIdRatioBased(0.05))`` sampler so child spans
   of a recorded root inherit the recording decision (no orphan spans) and the
   root is sampled at 5% — matches Decision 3 in the Sprint 8 plan.
4. Idempotent: subsequent calls are no-ops, so import-order in tests + Django
   app-loading is safe.

### Why split exporter selection on a single env var

We could ship a "console exporter" default in local dev, but a noisy stdout
on every test run is worse than the silence of the no-op exporter. CI runs
the OTel smoke (DRF-736 / G5) against ``InMemorySpanExporter`` directly,
which sidesteps this module entirely. Production picks the OTLP endpoint up
from `/etc/ai-bot-platform/.env`; staging mirrors the same path.

### What this module does NOT do

* Does **not** auto-instrument Django / requests / Celery. Auto-instrumentation
  is wired in :mod:`config.settings.base` via the ``OPENTELEMETRY_INSTRUMENT``
  env knob — we keep that decision close to LOGGING/CACHES rather than buried
  here.
* Does **not** emit spans itself. T2 (DRF-706) wraps the 19 pipeline steps;
  this module only configures the SDK so those spans have somewhere to go.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


_CONFIGURE_LOCK = threading.Lock()
_CONFIGURED = False


# Decision 3 — 5% root-span sampling. Children inherit the recording decision
# via ParentBased so a sampled trace stays whole.
_DEFAULT_SAMPLE_RATE = 0.05

# Decision 2 — OTLP/gRPC to a self-hosted collector.
_DEFAULT_OTLP_ENDPOINT = ""


def configure_otel() -> bool:
    """Wire the global OTel ``TracerProvider``.

    Returns ``True`` when the SDK was just configured, ``False`` when an
    earlier call already did the work. Caller doesn't have to check; the
    return value is for telemetry / health-probes.

    Safe to call from anywhere — including under ``DEBUG=True``, during
    ``manage.py shell``, or from a test fixture. Subsequent calls bail out
    cheap (boolean + lock) without ever importing the SDK.
    """
    global _CONFIGURED

    if _CONFIGURED:
        return False

    with _CONFIGURE_LOCK:
        if _CONFIGURED:
            return False

        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.trace.sampling import (
                ParentBased,
                TraceIdRatioBased,
            )
        except ImportError:  # pragma: no cover — optional dependency
            logger.warning("otel.configure_skipped reason=opentelemetry-sdk_not_installed")
            _CONFIGURED = True  # don't retry every request
            return False

        sample_rate = float(getattr(settings, "OTEL_TRACES_SAMPLE_RATE", _DEFAULT_SAMPLE_RATE))
        endpoint = str(
            getattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_OTLP_ENDPOINT) or ""
        )
        environment = str(getattr(settings, "DEPLOYMENT_ENVIRONMENT", "local") or "local")
        service_version = str(getattr(settings, "SERVICE_VERSION", "0.0.0") or "0.0.0")

        resource = Resource.create(
            {
                "service.name": "ai-bot-platform",
                "service.version": service_version,
                "deployment.environment": environment,
            }
        )
        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(sample_rate)),
        )

        exporter = _build_exporter(endpoint)
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter, max_export_batch_size=512))
            logger.info(
                "otel.configure ok endpoint=%s sample_rate=%s env=%s",
                endpoint,
                sample_rate,
                environment,
            )
        else:
            # No exporter = effectively a no-op TracerProvider. Spans still
            # carry trace_id/span_id (T3 + T4 read those) but never leave
            # the process.
            logger.info(
                "otel.configure noop reason=no_endpoint env=%s",
                environment,
            )

        trace.set_tracer_provider(provider)
        _CONFIGURED = True
        return True


def _build_exporter(endpoint: str) -> Any | None:
    """Construct the OTLP exporter — returns ``None`` when no endpoint is set
    or the exporter package isn't installed (local dev convenience).
    """
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:  # pragma: no cover — optional dependency
        logger.warning(
            "otel.exporter_unavailable reason=otlp_grpc_not_installed endpoint=%s",
            endpoint,
        )
        return None

    return OTLPSpanExporter(endpoint=endpoint, insecure=True)


def reset_otel_for_tests() -> None:
    """Test-only hook — pretends OTel was never configured.

    Tests that want to assert ``configure_otel`` did or did not do work
    flip this and re-call ``configure_otel``. The trace_provider is NOT
    reset (OTel API doesn't expose that publicly) — tests using a custom
    exporter should swap the provider themselves via
    ``trace.set_tracer_provider``.
    """
    global _CONFIGURED
    _CONFIGURED = False
