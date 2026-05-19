"""emit() — happy path, tenant from ContextVar, PII reject, soft validation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.eventbus import services, vocabulary as V
from apps.eventbus.models import DomainEvent
from apps.eventbus.services import EventbusPiiViolation
from apps.eventbus.ulid import is_valid
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _good_booking_data() -> dict:
    return {
        "booking_id": "b1",
        "customer_id": "c1",
        "service_id": "s1",
        "slot_start": "2026-05-19T10:00:00Z",
        "booking_source": "ai_direct",
    }


class TestEmitHappyPath:
    def test_inserts_row_with_envelope_fields(self):
        t = Tenant.objects.create(slug="evb-a", name="A")
        with tenant_scope(t):
            env = services.emit(V.BOOKING_CREATED, _good_booking_data(), actor_type="ai")

        row = DomainEvent.objects.get(event_id=env.event_id)
        assert row.event_name == V.BOOKING_CREATED
        assert row.event_version == "1.0"
        assert row.tenant_id == t.id
        assert row.actor == {"type": "ai", "id": None, "role": None}
        assert row.is_dispatched is False
        assert is_valid(row.event_id)

    def test_tenant_override_for_system_context(self):
        t = Tenant.objects.create(slug="evb-b", name="B")
        # No tenant_scope; pass tenant= explicitly.
        env = services.emit(V.BOOKING_CREATED, _good_booking_data(), actor_type="system", tenant=t)
        row = DomainEvent.objects.get(event_id=env.event_id)
        assert row.tenant_id == t.id

    def test_no_tenant_writes_null(self):
        env = services.emit(
            V.CUSTOMER_DELETED_REQUEST,
            {"customer_id": "c1", "requested_at": "2026-05-19T10:00:00Z"},
            actor_type="human",
            actor_role="customer",
        )
        row = DomainEvent.objects.get(event_id=env.event_id)
        assert row.tenant is None

    def test_typed_helper_booking_created(self):
        t = Tenant.objects.create(slug="evb-c", name="C")
        env = services.emit_booking_created(
            booking_id="b9",
            customer_id="c9",
            service_id="s9",
            slot_start="2026-05-19T10:00:00Z",
            booking_source="ai_direct",
            tenant=t,
        )
        row = DomainEvent.objects.get(event_id=env.event_id)
        assert row.event_name == V.BOOKING_CREATED
        assert row.data["booking_id"] == "b9"


class TestEmitPiiReject:
    def test_phone_key_rejects(self):
        with pytest.raises(EventbusPiiViolation):
            services.emit(
                V.BOOKING_CREATED,
                {**_good_booking_data(), "customer_phone": "+71234567890"},
                actor_type="system",
            )
        assert DomainEvent.objects.count() == 0

    def test_email_in_value_rejects(self):
        with pytest.raises(EventbusPiiViolation):
            services.emit(
                V.BOOKING_CREATED,
                {**_good_booking_data(), "note": "contact me at user@example.com"},
                actor_type="system",
            )
        assert DomainEvent.objects.count() == 0

    def test_pii_in_metadata_also_rejects(self):
        with pytest.raises(EventbusPiiViolation):
            services.emit(
                V.BOOKING_CREATED,
                _good_booking_data(),
                actor_type="system",
                metadata={"raw_text": "leaked"},
            )
        assert DomainEvent.objects.count() == 0


class TestEmitSoftValidation:
    def test_unknown_event_name_logs_warning_still_inserts(self, caplog):
        with caplog.at_level("WARNING", logger="apps.eventbus.services"):
            env = services.emit("totally.unknown.event", {"x": 1}, actor_type="system")
        assert DomainEvent.objects.filter(event_id=env.event_id).exists()
        assert any("unknown event_name" in rec.message for rec in caplog.records)

    def test_missing_required_keys_logs_warning_still_inserts(self, caplog):
        with caplog.at_level("WARNING", logger="apps.eventbus.services"):
            # booking.created without required keys
            env = services.emit(V.BOOKING_CREATED, {"booking_id": "x"}, actor_type="system")
        assert DomainEvent.objects.filter(event_id=env.event_id).exists()
        assert any("missing required keys" in rec.message for rec in caplog.records)


class TestEmitDbResilience:
    def test_db_insert_failure_does_not_raise(self, caplog):
        with patch(
            "apps.eventbus.models.DomainEvent.objects.create",
            side_effect=RuntimeError("DB down"),
        ):
            with caplog.at_level("ERROR", logger="apps.eventbus.services"):
                services.emit(V.BOOKING_CREATED, _good_booking_data(), actor_type="system")
        assert any("db_insert_failed" in rec.message for rec in caplog.records)


class TestVocabularyCoverage:
    """Phase 1 catalog must cover booking + customer + master domains."""

    def test_phase1_event_counts(self):
        booking = [n for n in V.CANONICAL_EVENTS if n.startswith("booking.")]
        customer = [n for n in V.CANONICAL_EVENTS if n.startswith("customer.")]
        master = [n for n in V.CANONICAL_EVENTS if n.startswith("master.")]
        assert len(booking) == 8  # taxonomy §3.1
        # §3.2: 6 original + customer.tier.changed (Loyalty Phase 2.a)
        assert len(customer) == 7
        assert len(master) == 8  # taxonomy §3.3

    def test_every_event_has_required_keys_schema(self):
        for name in V.CANONICAL_EVENTS:
            assert V.required_keys(name), f"{name} missing payload schema"
