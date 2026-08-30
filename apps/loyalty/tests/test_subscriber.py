"""LoyaltySubscriber on booking.completed envelopes (Phase 1.a).

Every ``booking.completed`` envelope below names a human closer. That is
not decoration: since the owner decision of 30.08 the subscriber credits
nothing for a visit closed by the clock, so an envelope without an actor
exercises the gate instead of the crediting path these tests are about.
The gate itself — and its positive counterpart on the same data — lives
in ``apps/bookings/tests/test_completed_by_gate.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogService
from apps.eventbus.envelope import Actor, Envelope
from apps.eventbus.ulid import new_ulid
from apps.identity.models import BotUser
from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent
from apps.loyalty.subscribers import LoyaltySubscriber
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: Кто закрыл визит, когда закрыл человек — см. docstring модуля.
HUMAN_CLOSER = "master:0d4f1a92-77c1-4c88-9b0e-2a6f3c5d8e10"


_FUTURE = dt.datetime(2027, 6, 15, 10, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="loy-sub", name="Loyalty Subscriber")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="loy-sub-bot")


@pytest.fixture
def service(tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=2,
        external_updated_at=dt.datetime.now(dt.UTC),
        name="Маникюр",
        price_from=Decimal("1200"),
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def booking(tenant, customer, service) -> BookingRequest:
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=customer,
        service=service,
        service_name=service.name,
        client_name="Customer",
        client_phone="snapshot",
        visit_at=_FUTURE,
        source="bot",
        booking_source="external",
    )


def _make_envelope(event_name: str, data: dict, tenant_id=None) -> Envelope:
    return Envelope(
        event_id=new_ulid(),
        event_name=event_name,
        event_version="1.0",
        occurred_at=dt.datetime.now(dt.UTC),
        actor=Actor(type="system"),
        data=data,
        tenant_id=tenant_id,
    )


class TestBookingCompletedHandling:
    def test_credits_points_per_default_rule(self, booking, settings):
        # 1200 ₽ → floor(1200/100) = 12 points
        settings.STRICT_TENANT_SCOPE = "strict"
        envelope = _make_envelope(
            "booking.completed",
            data={"booking_id": str(booking.pk), "completed_by": HUMAN_CLOSER},
            tenant_id=booking.tenant_id,
        )

        LoyaltySubscriber().handle(envelope)

        account = LoyaltyAccount.all_tenants.get(customer=booking.bot_user)
        assert account.balance == 12
        events = LoyaltyEvent.all_tenants.filter(account=account)
        assert events.count() == 1
        assert events.first().event_type == LoyaltyEvent.EventType.EARN_VISIT
        assert events.first().booking_id == booking.pk

    def test_minimum_5_points_for_zero_price(self, booking, service, settings):
        # Price=0 → MIN_VISIT_POINTS floor kicks in
        settings.STRICT_TENANT_SCOPE = "strict"
        service.price_from = Decimal("0")
        service.save(update_fields=["price_from"])
        envelope = _make_envelope(
            "booking.completed",
            data={"booking_id": str(booking.pk), "completed_by": HUMAN_CLOSER},
            tenant_id=booking.tenant_id,
        )

        LoyaltySubscriber().handle(envelope)

        account = LoyaltyAccount.all_tenants.get(customer=booking.bot_user)
        assert account.balance == 5

    def test_replay_is_idempotent(self, booking, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        envelope = _make_envelope(
            "booking.completed",
            data={"booking_id": str(booking.pk), "completed_by": HUMAN_CLOSER},
            tenant_id=booking.tenant_id,
        )

        sub = LoyaltySubscriber()
        sub.handle(envelope)
        sub.handle(envelope)  # second delivery — dispatcher retry

        account = LoyaltyAccount.all_tenants.get(customer=booking.bot_user)
        assert account.balance == 12  # not doubled
        assert (
            LoyaltyEvent.all_tenants.filter(
                account=account, event_type=LoyaltyEvent.EventType.EARN_VISIT
            ).count()
            == 1
        )


class TestSafeSkips:
    def test_other_events_ignored(self, booking, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        envelope = _make_envelope(
            "booking.created",
            data={"booking_id": str(booking.pk)},
            tenant_id=booking.tenant_id,
        )

        LoyaltySubscriber().handle(envelope)

        assert not LoyaltyAccount.all_tenants.filter(customer=booking.bot_user).exists()

    def test_missing_booking_id_is_safe(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        envelope = _make_envelope("booking.completed", data={"completed_by": HUMAN_CLOSER})
        # Must not raise.
        LoyaltySubscriber().handle(envelope)

    def test_unknown_booking_id_is_safe(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        envelope = _make_envelope(
            "booking.completed",
            data={"booking_id": str(uuid.uuid4()), "completed_by": HUMAN_CLOSER},
        )
        LoyaltySubscriber().handle(envelope)
        # No accounts created, no exceptions.

    def test_purged_bot_user_skip(self, booking, settings):
        # GDPR erasure leaves booking with bot_user=NULL.
        settings.STRICT_TENANT_SCOPE = "strict"
        BookingRequest.all_tenants.filter(pk=booking.pk).update(bot_user=None)
        envelope = _make_envelope(
            "booking.completed",
            data={"booking_id": str(booking.pk), "completed_by": HUMAN_CLOSER},
            tenant_id=booking.tenant_id,
        )

        LoyaltySubscriber().handle(envelope)
        # No crash; no account.


class TestErrorIsolation:
    def test_subscriber_never_propagates_exceptions(self, booking, settings, monkeypatch):
        # Even if downstream blows up, .handle() must return normally —
        # dispatcher relies on this contract.
        settings.STRICT_TENANT_SCOPE = "strict"
        from apps.loyalty import subscribers as sub_module

        def boom(*args, **kwargs):
            raise RuntimeError("simulated db failure")

        monkeypatch.setattr(sub_module.loyalty_services, "credit_points", boom)

        envelope = _make_envelope(
            "booking.completed",
            data={"booking_id": str(booking.pk), "completed_by": HUMAN_CLOSER},
            tenant_id=booking.tenant_id,
        )
        # No raise.
        LoyaltySubscriber().handle(envelope)
