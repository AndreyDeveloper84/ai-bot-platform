"""B3 — emit() canonical-envelope upgrade tests (DRF-461 / Sprint 3)."""

from __future__ import annotations

import logging
import uuid

import pytest

from apps.events.models import Event
from apps.events.services import emit
from apps.events.vocabulary import CONSENT_GRANTED
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="emit-canon", name="EC")


class TestCanonicalEmit:
    def test_canonical_call_populates_all_fields(self, tenant):
        dialog = uuid.uuid4()
        with tenant_scope(tenant), trace_id_scope("trace-1"):
            emit(
                CONSENT_GRANTED,
                distinct_id="bot-user-1",
                dialog_id=dialog,
                properties={"consent_type": "marketing"},
            )
        row = Event.objects.get(event_name=CONSENT_GRANTED, tenant=tenant)
        assert row.distinct_id == "bot-user-1"
        assert row.dialog_id == dialog
        assert row.properties == {"consent_type": "marketing"}
        assert row.trace_id == "trace-1"

    def test_canonical_call_mirrors_to_legacy_columns(self, tenant):
        """Sprint 1 audit subscribers still read event_type + payload."""

        with tenant_scope(tenant):
            emit(
                CONSENT_GRANTED,
                properties={"consent_type": "marketing"},
            )
        row = Event.objects.get(event_name=CONSENT_GRANTED, tenant=tenant)
        assert row.event_type == CONSENT_GRANTED
        assert row.payload == {"consent_type": "marketing"}


class TestLegacyBackCompat:
    def test_old_signature_still_works(self, tenant):
        """`emit("foo", payload={...})` from Sprint 1 callers keeps working."""

        with tenant_scope(tenant):
            emit("worker.handler_started", payload={"handler": "Echo"})
        row = Event.objects.get(event_type="worker.handler_started", tenant=tenant)
        # Legacy fields populated.
        assert row.payload == {"handler": "Echo"}
        # Canonical fields mirrored (event_name == event_type during back-compat).
        assert row.event_name == "worker.handler_started"
        assert row.properties == {"handler": "Echo"}

    def test_old_signature_no_distinct_or_dialog(self, tenant):
        """Legacy callers don't pass distinct_id/dialog_id — they remain empty/NULL."""

        with tenant_scope(tenant):
            emit("worker.handler_completed", payload={"handler": "Echo"})
        row = Event.objects.get(event_type="worker.handler_completed", tenant=tenant)
        assert row.distinct_id == ""
        assert row.dialog_id is None


class TestNonCanonicalWarn:
    def test_non_canonical_event_logs_warning_but_still_inserts(self, tenant, caplog):
        """Out-of-vocab event still lands in DB. Single warning surfaced."""

        with tenant_scope(tenant), caplog.at_level(logging.WARNING):
            emit("totally_made_up_event", properties={"x": 1})
        row = Event.objects.get(event_type="totally_made_up_event", tenant=tenant)
        assert row.event_name == "totally_made_up_event"
        assert any(
            "events.emit.non_canonical" in rec.message and "totally_made_up_event" in rec.message
            for rec in caplog.records
        )

    def test_canonical_event_does_not_warn(self, tenant, caplog):
        with tenant_scope(tenant), caplog.at_level(logging.WARNING):
            emit(CONSENT_GRANTED, properties={})
        non_canonical_warnings = [
            r for r in caplog.records if "events.emit.non_canonical" in r.message
        ]
        assert non_canonical_warnings == []


class TestPropertiesPrecedence:
    def test_properties_wins_over_payload_when_both_passed(self, tenant):
        """`properties` is canonical; `payload` is legacy fallback."""

        with tenant_scope(tenant):
            emit(
                CONSENT_GRANTED,
                payload={"legacy": True},
                properties={"canonical": True},
            )
        row = Event.objects.get(event_name=CONSENT_GRANTED, tenant=tenant)
        # Both columns get the canonical dict — properties wins.
        assert row.properties == {"canonical": True}
        assert row.payload == {"canonical": True}

    def test_payload_fills_properties_when_properties_missing(self, tenant):
        """Pure-legacy call site path: only payload set → mirrors into properties."""

        with tenant_scope(tenant):
            emit("worker.handler_started", payload={"handler": "x"})
        row = Event.objects.get(event_type="worker.handler_started", tenant=tenant)
        assert row.properties == {"handler": "x"}


class TestStillNeverRaises:
    def test_emit_never_raises_even_when_db_layer_throws(self, monkeypatch, tenant, caplog):
        """Contract carried over from E0 — telemetry must not break the request."""

        from apps.events.models import Event as RealEvent

        class _Boom:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("DB down")

        monkeypatch.setattr(RealEvent, "objects", _Boom())

        with tenant_scope(tenant), caplog.at_level(logging.ERROR):
            # Must NOT raise.
            emit(CONSENT_GRANTED, properties={"x": 1})
        assert any("events.emit_failed" in rec.message for rec in caplog.records)
