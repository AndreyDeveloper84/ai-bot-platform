"""Tests for Event + emit() helper (DRF-429 / E0)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.events.models import Event
from apps.events.services import emit
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class TestEmit:
    def test_persists_with_tenant_and_trace(self):
        t = Tenant.objects.create(slug="ev-a", name="A")
        with tenant_scope(t), trace_id_scope("trace-123"):
            emit("ingress.webhook_received", payload={"channel": "max"})

        row = Event.objects.get(event_type="ingress.webhook_received")
        assert row.tenant_id == t.id
        assert row.trace_id == "trace-123"
        assert row.payload == {"channel": "max"}

    def test_persists_without_context(self):
        # System-level events (no tenant + no trace) — still persist.
        emit("system.startup")
        row = Event.objects.get(event_type="system.startup")
        assert row.tenant is None
        assert row.trace_id == ""

    def test_payload_optional(self):
        with trace_id_scope("trace-only"):
            emit("trace.only")
        row = Event.objects.get(event_type="trace.only")
        assert row.payload == {}
        assert row.trace_id == "trace-only"

    def test_swallows_db_errors(self, caplog):
        # If the DB write blows up, emit() must NOT raise. Telemetry is
        # observational — must never break the surrounding request.
        with patch(
            "apps.events.models.Event.objects.create",
            side_effect=RuntimeError("DB down"),
        ):
            with caplog.at_level("ERROR", logger="apps.events.services"):
                emit("test.error")
        assert any("events.emit_failed" in rec.message for rec in caplog.records)


class TestEventModel:
    def test_str_includes_event_type_and_trace(self):
        row = Event.objects.create(event_type="x.y", trace_id="trace-x")
        s = str(row)
        assert "x.y" in s
        assert "trace-x" in s

    def test_indexed_lookups(self):
        # The (trace_id,) index is the one Sprint 5 replay will use heavily.
        # Confirm we can filter by trace_id and get expected rows.
        with trace_id_scope("trace-alpha"):
            emit("e1")
            emit("e2")
        with trace_id_scope("trace-beta"):
            emit("e3")

        alpha_events = list(
            Event.objects.filter(trace_id="trace-alpha").values_list("event_type", flat=True)
        )
        assert sorted(alpha_events) == ["e1", "e2"]
        beta_events = list(
            Event.objects.filter(trace_id="trace-beta").values_list("event_type", flat=True)
        )
        assert beta_events == ["e3"]


class TestTraceIdScope:
    def test_trace_id_resets_after_scope(self):
        from apps.tenancy.context import current_trace_id

        assert current_trace_id() is None
        with trace_id_scope("inner"):
            assert current_trace_id() == "inner"
        assert current_trace_id() is None

    def test_nested_scope_restores_outer(self):
        from apps.tenancy.context import current_trace_id

        with trace_id_scope("outer"):
            assert current_trace_id() == "outer"
            with trace_id_scope("inner"):
                assert current_trace_id() == "inner"
            assert current_trace_id() == "outer"
