"""PaymentEvent retention task tests (DRF-851 / Phase 1 / PI1).

Covers:

* Hard-delete of PaymentEvent rows older than the cutoff.
* Recent rows untouched.
* Audit row written with deleted count + cutoff + retention days.
* Idempotency — second run deletes 0 user rows.
* Default-day setting (90) when not overridden.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.orders.models import Order, PaymentEvent
from apps.orders.tasks import (
    AUDIT_PAYMENT_EVENT_CLEANUP,
    cleanup_old_payment_events,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


def _make_event(external_event_id: str, *, days_old: int = 0) -> PaymentEvent:
    """Helper: create a PaymentEvent then back-date received_at."""
    evt = PaymentEvent.objects.create(
        external_event_id=external_event_id,
        event_type="payment.succeeded",
        raw_payload={"id": external_event_id},
    )
    if days_old > 0:
        PaymentEvent.objects.filter(pk=evt.pk).update(
            received_at=timezone.now() - timedelta(days=days_old),
        )
    return evt


class TestCleanupOldPaymentEvents:
    def test_deletes_events_older_than_cutoff(self, settings):
        settings.PAYMENT_EVENT_RETENTION_DAYS = 30

        old = _make_event("evt-old-1", days_old=31)
        recent = _make_event("evt-recent-1", days_old=0)

        deleted = cleanup_old_payment_events()
        assert deleted == 1
        assert not PaymentEvent.objects.filter(pk=old.pk).exists()
        assert PaymentEvent.objects.filter(pk=recent.pk).exists()

    def test_default_retention_is_90_days(self, settings):
        if hasattr(settings, "PAYMENT_EVENT_RETENTION_DAYS"):
            del settings.PAYMENT_EVENT_RETENTION_DAYS

        old = _make_event("evt-old-91", days_old=91)
        recent = _make_event("evt-recent-30", days_old=30)

        deleted = cleanup_old_payment_events()
        assert deleted == 1
        assert not PaymentEvent.objects.filter(pk=old.pk).exists()
        assert PaymentEvent.objects.filter(pk=recent.pk).exists()

    def test_returns_zero_when_nothing_to_delete(self, settings):
        settings.PAYMENT_EVENT_RETENTION_DAYS = 30
        _make_event("evt-fresh", days_old=0)
        deleted = cleanup_old_payment_events()
        assert deleted == 0

    def test_writes_audit_row(self, settings):
        settings.PAYMENT_EVENT_RETENTION_DAYS = 30
        _make_event("evt-audited", days_old=31)

        cleanup_old_payment_events()
        row = AuditLog.all_tenants.get(action=AUDIT_PAYMENT_EVENT_CLEANUP)
        assert row.payload["deleted"] == 1
        assert row.payload["retention_days"] == 30
        assert "cutoff" in row.payload
        # System action — no tenant context.
        assert row.tenant is None

    def test_audit_row_written_when_nothing_to_delete(self, settings):
        settings.PAYMENT_EVENT_RETENTION_DAYS = 30
        _make_event("evt-still-fresh", days_old=0)
        cleanup_old_payment_events()
        row = AuditLog.all_tenants.get(action=AUDIT_PAYMENT_EVENT_CLEANUP)
        assert row.payload["deleted"] == 0

    def test_cleanup_is_idempotent(self, settings):
        settings.PAYMENT_EVENT_RETENTION_DAYS = 30
        _make_event("evt-idempotent", days_old=31)

        first = cleanup_old_payment_events()
        assert first == 1

        second = cleanup_old_payment_events()
        assert second == 0
        # Both runs still wrote audit rows.
        assert AuditLog.all_tenants.filter(action=AUDIT_PAYMENT_EVENT_CLEANUP).count() == 2

    def test_unlinked_event_still_deleted(self, settings):
        # PaymentEvent.order can be NULL (webhook for an unknown order
        # id is forensic evidence). Cleanup must still sweep these.
        settings.PAYMENT_EVENT_RETENTION_DAYS = 30
        evt = _make_event("evt-orphan", days_old=31)
        assert evt.order is None

        deleted = cleanup_old_payment_events()
        assert deleted == 1

    def test_linked_event_swept_without_touching_order(self, settings):
        # PaymentEvent.order is SET_NULL on order delete; cleanup
        # deletes only the event row, never the linked Order.
        settings.PAYMENT_EVENT_RETENTION_DAYS = 30
        tenant = Tenant.objects.create(slug="pe-cleanup-t", name="PE Cleanup T")
        order = Order.all_tenants.create(
            tenant=tenant,
            kind=Order.Kind.CERTIFICATE,
            amount_rub=5000,
        )
        evt = PaymentEvent.objects.create(
            external_event_id="evt-linked",
            event_type="payment.succeeded",
            order=order,
            raw_payload={},
        )
        PaymentEvent.objects.filter(pk=evt.pk).update(
            received_at=timezone.now() - timedelta(days=31),
        )

        deleted = cleanup_old_payment_events()
        assert deleted == 1
        # Order survives.
        assert Order.all_tenants.filter(pk=order.pk).exists()
