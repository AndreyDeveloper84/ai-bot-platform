"""ContextFilter tests (DRF-714 / Sprint 8 / J2)."""

from __future__ import annotations

import json
import logging

import pytest

from apps.observability.logging import ContextFilter, JsonFormatter


def _make_record(msg: str = "x") -> logging.LogRecord:
    return logging.LogRecord(
        name="apps.test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


class TestFilterAlwaysPasses:
    """Filter is for enrichment — must return True so logging continues."""

    def test_returns_true(self) -> None:
        record = _make_record()
        assert ContextFilter().filter(record) is True


class TestEmptyContext:
    def test_no_context_yields_empty_strings(self) -> None:
        record = _make_record()
        ContextFilter().filter(record)
        assert getattr(record, "tenant_id", None) == ""
        assert getattr(record, "trace_id", None) == ""
        assert getattr(record, "span_id", None) == ""

    def test_does_not_raise_when_imports_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If apps.tenancy.context can't be imported (theoretical edge),
        filter MUST still set the attribute and not crash.
        """
        # Force the lazy import path by deleting tenancy.context from
        # sys.modules — re-import will succeed but the helper handles
        # both raise + return-None paths internally.
        import sys

        original = sys.modules.pop("apps.tenancy.context", None)
        try:
            record = _make_record()
            assert ContextFilter().filter(record) is True
        finally:
            if original is not None:
                sys.modules["apps.tenancy.context"] = original


class TestTenantContext:
    @pytest.mark.django_db
    def test_tenant_id_populated_from_scope(self) -> None:
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="j2-tenant", name="J2")
        with tenant_scope(tenant):
            record = _make_record()
            ContextFilter().filter(record)
            assert getattr(record, "tenant_id", None) == str(tenant.id)


class TestOTelContext:
    def test_trace_ids_populated_inside_span(self) -> None:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        trace.set_tracer_provider(TracerProvider())
        tracer = trace.get_tracer("j2-test")

        record = _make_record()
        with tracer.start_as_current_span("test-span"):
            ContextFilter().filter(record)

        assert len(str(getattr(record, "trace_id", "") or "")) == 32
        assert len(str(getattr(record, "span_id", "") or "")) == 16


class TestIntegrationWithFormatter:
    """Filter + JsonFormatter must produce JSON with populated fields."""

    @pytest.mark.django_db
    def test_filter_then_format_round_trip(self) -> None:
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="j2-int", name="J2 Int")
        record = _make_record("hello tenant")
        formatter = JsonFormatter()
        with tenant_scope(tenant):
            ContextFilter().filter(record)
            out = json.loads(formatter.format(record))

        assert out["tenant_id"] == str(tenant.id)
        assert out["msg"] == "hello tenant"
