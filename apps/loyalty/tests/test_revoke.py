"""Phase 1.b — revoke_visit_points + booking.cancelled subscriber path."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogService
from apps.eventbus.envelope import Actor, Envelope
from apps.eventbus.ulid import new_ulid
from apps.identity.models import BotUser
from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent
from apps.loyalty.services import (
    credit_points,
    get_or_create_account,
    redeem_points,
    revoke_visit_points,
)
from apps.loyalty.subscribers import LoyaltySubscriber
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


_FUTURE = dt.datetime(2027, 6, 15, 10, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="loy-rev", name="LoyaltyRevoke")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="loy-rev-bot")


@pytest.fixture
def service(tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
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


def _earned(tenant, customer, booking, points=12):
    """Set up: account exists, has earned `points` from this booking."""
    with tenant_scope(tenant):
        account = get_or_create_account(customer)
        credit_points(
            account,
            points=points,
            event_type=LoyaltyEvent.EventType.EARN_VISIT,
            booking=booking,
            reason="earned in test setup",
        )
        account.refresh_from_db()
    return account


def _cancel_envelope(booking, tenant) -> Envelope:
    return Envelope(
        event_id=new_ulid(),
        event_name="booking.cancelled",
        event_version="1.0",
        occurred_at=dt.datetime.now(dt.UTC),
        actor=Actor(type="human", role="customer"),
        data={
            "booking_id": str(booking.pk),
            "cancelled_by": "customer",
            "cancellation_reason": "changed_mind",
            "cancelled_at": dt.datetime.now(dt.UTC).isoformat(),
        },
        tenant_id=tenant.id,
    )


class TestRevokeServiceHappy:
    def test_revokes_full_earned_amount(self, tenant, customer, booking):
        account = _earned(tenant, customer, booking, points=12)
        with tenant_scope(tenant):
            event = revoke_visit_points(account, booking=booking, reason="cancel")
        assert event is not None
        assert event.points_delta == -12
        assert event.balance_after == 0
        account.refresh_from_db()
        assert account.balance == 0

    def test_metadata_carries_earned_amount(self, tenant, customer, booking):
        account = _earned(tenant, customer, booking, points=12)
        with tenant_scope(tenant):
            event = revoke_visit_points(account, booking=booking)
        assert event.metadata["earned_points"] == 12
        assert event.metadata["revoke_amount"] == 12
        assert "underflow" not in event.metadata


class TestRevokeSkipPaths:
    def test_no_earn_event_returns_none(self, tenant, customer, booking):
        # Customer cancelled before any visit completed → no points to
        # revoke. This is the COMMON path (most cancellations happen
        # pre-completion).
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            result = revoke_visit_points(account, booking=booking)
        assert result is None
        account.refresh_from_db()
        assert account.balance == 0

    def test_idempotent_re_revoke(self, tenant, customer, booking):
        # Dispatcher retry of booking.cancelled envelope MUST NOT
        # double-debit the balance.
        account = _earned(tenant, customer, booking, points=12)
        with tenant_scope(tenant):
            first = revoke_visit_points(account, booking=booking)
            second = revoke_visit_points(account, booking=booking)
        assert first is not None
        assert second is None  # idempotent no-op
        account.refresh_from_db()
        assert account.balance == 0  # not -12


class TestBalanceClamp:
    def test_underflow_clamps_at_zero(self, tenant, customer, booking, service):
        # Customer earned 12 points, then spent them all elsewhere (e.g.
        # admin manual adjust DOWN), THEN the booking gets cancelled.
        # Handoff §14: balance MUST NOT go negative — clamp at 0 + note.
        account = _earned(tenant, customer, booking, points=12)

        # Spend the 12 points via a separate redemption-like adjust.
        # Use redeem_points with sufficient visit price to make it pass
        # the 30% cap (need visit ≥ floor(12 / 0.3) = 40 ₽; pick 200).
        # But min redemption is 50, so directly drain via a manual adjust
        # on the model layer (test-only hack).
        another_booking = BookingRequest.objects.create(
            tenant=tenant,
            bot_user=customer,
            service=service,
            service_name=service.name,
            client_name="C2",
            client_phone="snap",
            visit_at=_FUTURE + dt.timedelta(days=1),
            source="bot",
            booking_source="external",
        )
        # Boost balance so redeem clears the 50-min, then debit explicitly.
        with tenant_scope(tenant):
            # Add 50 more so we can redeem 50 against a 200₽ visit (cap=60).
            credit_points(
                account,
                points=50,
                event_type=LoyaltyEvent.EventType.MANUAL_ADJUST,
                reason="seed for clamp test",
            )
            account.refresh_from_db()
            assert account.balance == 62
            redeem_points(
                account,
                points=50,
                visit_price_rub=200,  # 30% = 60, ≥ 50 ✅
                booking=another_booking,
            )
            account.refresh_from_db()
            # Now balance = 12.
            assert account.balance == 12

            # Drain it further to 5 via a tiny REDEEM (min 50 blocks),
            # so use direct manual_adjust-style debit by creating a
            # synthetic event row. Instead — just spend in another redeem.
            another_booking_2 = BookingRequest.objects.create(
                tenant=tenant,
                bot_user=customer,
                service=service,
                service_name=service.name,
                client_name="C3",
                client_phone="snap",
                visit_at=_FUTURE + dt.timedelta(days=2),
                source="bot",
                booking_source="external",
            )
            # Top up so we can redeem another 50.
            credit_points(
                account,
                points=40,
                event_type=LoyaltyEvent.EventType.MANUAL_ADJUST,
                reason="top up",
            )
            account.refresh_from_db()
            # Balance now 52. Redeem 50.
            redeem_points(
                account,
                points=50,
                visit_price_rub=200,
                booking=another_booking_2,
            )
            account.refresh_from_db()
            # Balance now 2 (less than the 12 we'd want to revoke).
            assert account.balance == 2

            # Now revoke the original 12-point earn — only 2 available.
            event = revoke_visit_points(account, booking=booking)

        assert event is not None
        assert event.points_delta == -2  # clamped
        assert event.balance_after == 0
        assert event.metadata["earned_points"] == 12
        assert event.metadata["revoke_amount"] == 2
        assert event.metadata["underflow"] == 10
        assert event.metadata["clamp_reason"] == "balance_already_redeemed"
        account.refresh_from_db()
        assert account.balance == 0


class TestSubscriberRevokePath:
    def test_cancel_envelope_revokes(self, tenant, customer, booking):
        # Earn first, then deliver booking.cancelled.
        _earned(tenant, customer, booking, points=12)
        envelope = _cancel_envelope(booking, tenant)

        LoyaltySubscriber().handle(envelope)

        account = LoyaltyAccount.all_tenants.get(customer=customer)
        assert account.balance == 0
        revokes = LoyaltyEvent.all_tenants.filter(
            account=account, event_type=LoyaltyEvent.EventType.REFUND_REVOKE
        )
        assert revokes.count() == 1
        assert revokes.first().booking_id == booking.pk

    def test_cancel_before_completion_is_noop(self, tenant, customer, booking):
        # No earn event was ever created. The customer might not even
        # have a LoyaltyAccount. Subscriber must NOT crash.
        envelope = _cancel_envelope(booking, tenant)

        LoyaltySubscriber().handle(envelope)

        # No account materialized (we don't create accounts on revoke
        # path — they only exist after earning).
        assert not LoyaltyAccount.all_tenants.filter(customer=customer).exists()

    def test_cancel_replay_idempotent(self, tenant, customer, booking):
        _earned(tenant, customer, booking, points=12)
        envelope = _cancel_envelope(booking, tenant)

        sub = LoyaltySubscriber()
        sub.handle(envelope)
        sub.handle(envelope)

        account = LoyaltyAccount.all_tenants.get(customer=customer)
        assert account.balance == 0  # not double-debited (would be -12)
        assert (
            LoyaltyEvent.all_tenants.filter(
                account=account, event_type=LoyaltyEvent.EventType.REFUND_REVOKE
            ).count()
            == 1
        )

    def test_purged_bot_user_safe(self, tenant, customer, booking):
        BookingRequest.all_tenants.filter(pk=booking.pk).update(bot_user=None)
        envelope = _cancel_envelope(booking, tenant)
        # Must not crash.
        LoyaltySubscriber().handle(envelope)

    def test_unknown_booking_safe(self, tenant):
        envelope = Envelope(
            event_id=new_ulid(),
            event_name="booking.cancelled",
            event_version="1.0",
            occurred_at=dt.datetime.now(dt.UTC),
            actor=Actor(type="human", role="customer"),
            data={"booking_id": "00000000-0000-0000-0000-000000000000"},
            tenant_id=tenant.id,
        )
        LoyaltySubscriber().handle(envelope)


class TestSubscriberDispatchTable:
    """Subscriber must continue to handle booking.completed correctly
    after the cancel-routing addition. Regression guard."""

    def test_completed_still_credits(self, tenant, customer, booking):
        envelope = Envelope(
            event_id=new_ulid(),
            event_name="booking.completed",
            event_version="1.0",
            occurred_at=dt.datetime.now(dt.UTC),
            actor=Actor(type="system"),
            data={"booking_id": str(booking.pk)},
            tenant_id=tenant.id,
        )

        LoyaltySubscriber().handle(envelope)

        account = LoyaltyAccount.all_tenants.get(customer=customer)
        assert account.balance == 12

    def test_unrelated_event_ignored(self, tenant, customer, booking):
        envelope = Envelope(
            event_id=new_ulid(),
            event_name="customer.consent.changed",
            event_version="1.0",
            occurred_at=dt.datetime.now(dt.UTC),
            actor=Actor(type="system"),
            data={"customer_id": str(customer.id)},
            tenant_id=tenant.id,
        )

        LoyaltySubscriber().handle(envelope)

        assert not LoyaltyAccount.all_tenants.filter(customer=customer).exists()
