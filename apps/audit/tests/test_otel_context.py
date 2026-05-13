"""OTel trace_id injection into AuditLog payload (DRF-707 / Sprint 8 / T3)."""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from apps.audit.models import AuditLog
from apps.audit.services import write_audit


@pytest.mark.django_db
class TestOtelContextInjection:
    """write_audit() merges the current OTel span context into payload."""

    def test_inside_span_payload_carries_trace_id(self) -> None:
        trace.set_tracer_provider(TracerProvider())
        tracer = trace.get_tracer("t3-test")

        with tracer.start_as_current_span("test-span"):
            write_audit("t3.inside_span", payload={"foo": "bar"})

        row = AuditLog.all_tenants.get(action="t3.inside_span")
        assert "trace_id" in row.payload
        assert "span_id" in row.payload
        assert len(row.payload["trace_id"]) == 32  # OTel 16-byte hex
        assert len(row.payload["span_id"]) == 16  # OTel 8-byte hex
        # Caller payload preserved.
        assert row.payload["foo"] == "bar"

    def test_outside_span_no_trace_id_no_crash(self) -> None:
        """write_audit MUST be safe to call from system tasks / CLI / tests
        without OTel context set up.
        """
        write_audit("t3.outside_span", payload={"foo": "bar"})

        row = AuditLog.all_tenants.get(action="t3.outside_span")
        assert "trace_id" not in row.payload
        assert "span_id" not in row.payload
        assert row.payload["foo"] == "bar"

    def test_payload_supplied_trace_id_not_clobbered(self) -> None:
        """When the caller passes a `trace_id` explicitly, the OTel
        default does NOT override it (test fixtures sometimes pin
        specific values).
        """
        trace.set_tracer_provider(TracerProvider())
        tracer = trace.get_tracer("t3-pin")

        with tracer.start_as_current_span("pin-span"):
            write_audit(
                "t3.pinned_trace",
                payload={"trace_id": "f" * 32, "span_id": "1" * 16},
            )

        row = AuditLog.all_tenants.get(action="t3.pinned_trace")
        assert row.payload["trace_id"] == "f" * 32
        assert row.payload["span_id"] == "1" * 16

    def test_input_payload_not_mutated(self) -> None:
        """The caller-passed dict must NOT be mutated in place."""
        trace.set_tracer_provider(TracerProvider())
        tracer = trace.get_tracer("t3-no-mutate")

        caller_payload: dict[str, Any] = {"foo": "bar"}
        with tracer.start_as_current_span("nm-span"):
            write_audit("t3.no_mutate", payload=caller_payload)

        # The caller's dict is unchanged.
        assert caller_payload == {"foo": "bar"}
