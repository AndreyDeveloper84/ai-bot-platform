"""Reminder factory tests (DRF-844 / Phase 1 / R1).

Covers:

* both rows created (DAY_BEFORE + TWO_HOURS) with correct offsets
* idempotency (calling twice yields the same 2 rows, not 4)
* empty chat_id → no rows + log + empty return list
* cross-tenant scoping (factory writes to the tenant FK, not
  current_tenant)
* booking_request linkage (set / unset paths)
* PENDING reset on re-confirm of a previously-CANCELLED reminder
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, BookingRequest
from apps.bookings.reminders_factory import create_reminders_for_booking
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rem-factory-a", name="Salon A")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="rem-factory-b", name="Salon B")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-1",
        chat_id="chat-1",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def bot_user_no_chat(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-no-chat",
        chat_id="",  # No reachable channel.
        phone="79990000000",
        client_name="Boris",
    )


class TestCreateReminders:
    def test_creates_both_kinds(self, tenant: Tenant, bot_user: BotUser) -> None:
        visit_at = timezone.now() + timedelta(days=1)
        saved = create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-1",
            visit_at=visit_at,
            master_name="Lera",
            service_name="Массаж",
        )
        assert len(saved) == 2
        kinds = {r.kind for r in saved}
        assert kinds == {
            BookingReminder.Kind.DAY_BEFORE,
            BookingReminder.Kind.TWO_HOURS,
        }

    def test_day_before_scheduled_24h_earlier(self, tenant: Tenant, bot_user: BotUser) -> None:
        visit_at = timezone.now() + timedelta(days=2)
        saved = create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-2",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        day_before = next(r for r in saved if r.kind == BookingReminder.Kind.DAY_BEFORE)
        assert day_before.scheduled_at == visit_at - timedelta(hours=24)

    def test_two_hours_scheduled_2h_earlier(self, tenant: Tenant, bot_user: BotUser) -> None:
        visit_at = timezone.now() + timedelta(days=2)
        saved = create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-3",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        two_h = next(r for r in saved if r.kind == BookingReminder.Kind.TWO_HOURS)
        assert two_h.scheduled_at == visit_at - timedelta(hours=2)

    def test_snapshot_fields_persisted(self, tenant: Tenant, bot_user: BotUser) -> None:
        visit_at = timezone.now() + timedelta(days=1)
        saved = create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-4",
            visit_at=visit_at,
            master_name="Lera",
            service_name="Массаж классический",
        )
        for row in saved:
            assert row.master_name == "Lera"
            assert row.service_name == "Массаж классический"
            assert row.chat_id == "chat-1"
            assert row.tenant_id == tenant.id
            assert row.status == BookingReminder.Status.PENDING


class TestIdempotency:
    def test_double_call_yields_two_rows_not_four(self, tenant: Tenant, bot_user: BotUser) -> None:
        visit_at = timezone.now() + timedelta(days=1)
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-idem",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-idem",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        total = BookingReminder.all_tenants.filter(yclients_record_id="yc-idem").count()
        assert total == 2

    def test_re_invocation_resets_cancelled_to_pending(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """A previously-cancelled reminder must un-cancel on re-confirm."""
        visit_at = timezone.now() + timedelta(days=1)
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-recancel",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        # Simulate a cancel (mid-flow).
        BookingReminder.all_tenants.filter(yclients_record_id="yc-recancel").update(
            status=BookingReminder.Status.CANCELLED,
        )
        # Re-confirm same booking.
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-recancel",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        rows = BookingReminder.all_tenants.filter(yclients_record_id="yc-recancel")
        assert rows.count() == 2
        for row in rows:
            assert row.status == BookingReminder.Status.PENDING

    def test_re_invocation_updates_visit_at(self, tenant: Tenant, bot_user: BotUser) -> None:
        original_visit = timezone.now() + timedelta(days=1)
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-resched",
            visit_at=original_visit,
            master_name="M",
            service_name="S",
        )
        new_visit = timezone.now() + timedelta(days=2)
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-resched",
            visit_at=new_visit,
            master_name="M",
            service_name="S",
        )
        rows = BookingReminder.all_tenants.filter(yclients_record_id="yc-resched")
        for row in rows:
            assert row.visit_at == new_visit


class TestEmptyChatId:
    def test_skipped_when_chat_id_empty(self, tenant: Tenant, bot_user_no_chat: BotUser) -> None:
        visit_at = timezone.now() + timedelta(days=1)
        saved = create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user_no_chat,
            yclients_record_id="yc-no-chat",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        assert saved == []
        # No rows written.
        assert BookingReminder.all_tenants.filter(yclients_record_id="yc-no-chat").count() == 0


class TestCrossTenant:
    def test_writes_to_passed_tenant_not_current_tenant(
        self,
        tenant: Tenant,
        tenant_b: Tenant,
        bot_user: BotUser,
    ) -> None:
        """Factory uses all_tenants — the passed tenant FK is authoritative,
        not the current_tenant ContextVar (which may be unset in beat
        / webhook contexts).
        """
        visit_at = timezone.now() + timedelta(days=1)
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-tenant",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        # Tenant A's rows exist (cross-tenant lookup).
        a_rows = BookingReminder.all_tenants.filter(tenant=tenant)
        assert a_rows.count() == 2
        # Tenant B has no rows.
        b_rows = BookingReminder.all_tenants.filter(tenant=tenant_b)
        assert b_rows.count() == 0


class TestBookingRequestLink:
    def test_links_when_passed(self, tenant: Tenant, bot_user: BotUser) -> None:
        booking = BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="S",
            client_name="A",
            client_phone="+79991234567",
        )
        visit_at = timezone.now() + timedelta(days=1)
        saved = create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-linked",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
            booking_request=booking,
        )
        for row in saved:
            assert row.booking_request_id == booking.id

    def test_no_link_when_omitted(self, tenant: Tenant, bot_user: BotUser) -> None:
        visit_at = timezone.now() + timedelta(days=1)
        saved = create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="yc-unlinked",
            visit_at=visit_at,
            master_name="M",
            service_name="S",
        )
        for row in saved:
            assert row.booking_request_id is None
