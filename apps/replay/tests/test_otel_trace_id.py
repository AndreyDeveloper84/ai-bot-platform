"""OTel trace_id preference in replay recorder (DRF-708 / Sprint 8 / T4)."""

from __future__ import annotations

import uuid

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from apps.replay.models import ReplayTrace
from apps.replay.recorder import TraceRecorder, _prefer_otel_trace_id
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="t4-tenant", name="T4")


@pytest.fixture(autouse=True)
def _force_sampling(settings) -> None:
    settings.REPLAY_SAMPLE_RATE_TEST = 1.0


class TestHelper:
    def test_no_span_returns_fallback(self) -> None:
        fallback = str(uuid.uuid4())
        assert _prefer_otel_trace_id(fallback) == fallback

    def test_with_span_returns_otel_trace_id(self) -> None:
        trace.set_tracer_provider(TracerProvider())
        tracer = trace.get_tracer("t4-helper")

        with tracer.start_as_current_span("test-span"):
            result = _prefer_otel_trace_id("ignored-fallback")

        assert len(result) == 32  # OTel 16-byte hex
        assert result != "ignored-fallback"


class TestRecorderIntegration:
    @pytest.mark.django_db
    def test_replay_trace_uses_otel_trace_id_when_span_active(self, tenant: Tenant) -> None:
        trace.set_tracer_provider(TracerProvider())
        tracer = trace.get_tracer("t4-record")

        with tenant_scope(tenant), tracer.start_as_current_span("recorder-test") as span:
            otel_hex = format(span.get_span_context().trace_id, "032x")
            TraceRecorder().capture("uuid7-fallback", [{"step": "x"}])

        row = ReplayTrace.all_tenants.get(tenant=tenant)
        # Stored trace_id is OTel-derived, NOT the caller's uuid7.
        assert row.trace_id == otel_hex

    @pytest.mark.django_db
    def test_replay_trace_uses_fallback_outside_span(self, tenant: Tenant) -> None:
        # No tracer-set-up here. The default OTel provider returns an
        # INVALID context; recorder must fall back to caller-supplied UUID.
        fallback = str(uuid.uuid4())
        with tenant_scope(tenant):
            TraceRecorder().capture(fallback, [{"step": "x"}])

        row = ReplayTrace.all_tenants.get(tenant=tenant)
        assert str(row.trace_id) == fallback
