"""BookingRequest + BookingReminder model tests (DRF-838 / Phase 1 / B2).

Covers:

* Happy-path create (both models)
* TenantScopedManager filters by current_tenant (cross-tenant leak guard)
* Idempotency constraint on (yclients_record_id, kind) fires on duplicate
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.booking.models import BookingReminder, BookingRequest
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant_a(db) -> Tenant:
    return Tenant.objects.create(slug="salon-a", name="Salon A")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="salon-b", name="Salon B")


@pytest.fixture
def bot_user_a(tenant_a: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_a,
        channel="max",
        channel_user_id="111",
        chat_id="111",
        phone="79991234567",
        client_name="Anna",
    )


class TestBookingRequest:
    def test_create_minimal(self, tenant_a: Tenant, bot_user_a: BotUser) -> None:
        with tenant_scope(tenant_a):
            req = BookingRequest.objects.create(
                tenant=tenant_a,
                bot_user=bot_user_a,
                service_name="Massage",
                client_name="Anna",
                client_phone="+79991234567",
                source="yclients_admin",
            )
        assert req.is_processed is False  # default
        assert req.comment == ""

    def test_str_representation(self, tenant_a: Tenant) -> None:
        with tenant_scope(tenant_a):
            req = BookingRequest.objects.create(
                tenant=tenant_a,
                service_name="Pedicure",
                client_name="Maria",
                client_phone="+79990000000",
            )
        assert "Maria" in str(req)
        assert "Pedicure" in str(req)


class TestBookingRequestTenantScope:
    def test_default_manager_filters_by_current_tenant(
        self, tenant_a: Tenant, tenant_b: Tenant
    ) -> None:
        BookingRequest.all_tenants.create(
            tenant=tenant_a,
            service_name="A",
            client_name="A",
            client_phone="+71",
        )
        BookingRequest.all_tenants.create(
            tenant=tenant_b,
            service_name="B",
            client_name="B",
            client_phone="+72",
        )

        with tenant_scope(tenant_a):
            assert BookingRequest.objects.count() == 1
            assert BookingRequest.objects.get().service_name == "A"

        with tenant_scope(tenant_b):
            assert BookingRequest.objects.count() == 1
            assert BookingRequest.objects.get().service_name == "B"

        # all_tenants escape hatch sees both.
        assert BookingRequest.all_tenants.count() == 2


class TestBookingReminder:
    def test_create_minimal(self, tenant_a: Tenant, bot_user_a: BotUser) -> None:
        visit_at = timezone.now() + timedelta(days=1)
        with tenant_scope(tenant_a):
            rem = BookingReminder.objects.create(
                tenant=tenant_a,
                bot_user=bot_user_a,
                yclients_record_id="yc-1",
                chat_id="111",
                visit_at=visit_at,
                kind=BookingReminder.Kind.DAY_BEFORE,
                scheduled_at=visit_at - timedelta(hours=24),
            )
        assert rem.status == BookingReminder.Status.PENDING

    def test_unique_together_yclients_id_kind(
        self, tenant_a: Tenant, bot_user_a: BotUser
    ) -> None:
        """The idempotency cornerstone — same (yc_id, kind) pair twice fails."""
        visit_at = timezone.now() + timedelta(days=1)
        BookingReminder.all_tenants.create(
            tenant=tenant_a,
            bot_user=bot_user_a,
            yclients_record_id="yc-42",
            chat_id="111",
            visit_at=visit_at,
            kind=BookingReminder.Kind.DAY_BEFORE,
            scheduled_at=visit_at - timedelta(hours=24),
        )
        with pytest.raises(IntegrityError):
            BookingReminder.all_tenants.create(
                tenant=tenant_a,
                bot_user=bot_user_a,
                yclients_record_id="yc-42",
                chat_id="111",
                visit_at=visit_at,
                kind=BookingReminder.Kind.DAY_BEFORE,
                scheduled_at=visit_at - timedelta(hours=24),
            )

    def test_status_choices_locked(self) -> None:
        # B2 locks the full reminder lifecycle vocabulary so B3 (skill
        # tools + Celery dispatchers) lands without a schema migration.
        assert set(BookingReminder.Status.values) == {
            "pending",
            "sent_no_reply",
            "sent",
            "confirmed",
            "reschedule",
            "cancelled",
            "escalated",
            "failed",
        }
