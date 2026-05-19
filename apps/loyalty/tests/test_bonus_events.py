"""Phase 2.d — long-return + referral bonus events."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogService
from apps.identity.models import BotUser
from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent, LoyaltyReferral
from apps.loyalty.services import (
    LONG_RETURN_BONUS_POINTS,
    REFERRAL_BONUS_POINTS,
    create_referral,
    credit_points,
    get_or_create_account,
    opt_out,
    revoke_visit_points,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


_FUTURE = dt.datetime(2027, 6, 15, 10, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="loy-bonus", name="LoyaltyBonus")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="loy-bonus-bot")


@pytest.fixture
def service(tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=100,
        external_updated_at=dt.datetime.now(dt.UTC),
        name="Маникюр",
        price_from=Decimal("1200"),
        duration_min=60,
        is_active=True,
    )


def _booking(tenant, customer, service, suffix: str) -> BookingRequest:
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=customer,
        service=service,
        service_name=service.name,
        client_name=f"C-{suffix}",
        client_phone="snap",
        visit_at=_FUTURE + dt.timedelta(days=int(suffix)),
        source="bot",
        booking_source="external",
    )


def _backdate_event(event: LoyaltyEvent, days_ago: int) -> None:
    """Force occurred_at backwards for testing time-window logic."""
    LoyaltyEvent.all_tenants.filter(pk=event.pk).update(
        occurred_at=timezone.now() - dt.timedelta(days=days_ago)
    )


class TestLongReturnBonus:
    def test_first_ever_visit_no_bonus(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
            )

        assert not LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.EARN_RETURN
        ).exists()

    def test_91_day_gap_credits_bonus(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            first = credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
            )
            # Push first visit's occurred_at 91 days into the past.
            _backdate_event(first, days_ago=91)

            # Second visit "today".
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "1"),
            )

        bonus = LoyaltyEvent.all_tenants.filter(event_type=LoyaltyEvent.EventType.EARN_RETURN)
        assert bonus.count() == 1
        assert bonus.first().points_delta == LONG_RETURN_BONUS_POINTS
        account.refresh_from_db()
        assert account.balance == 12 + 12 + LONG_RETURN_BONUS_POINTS

    def test_89_day_gap_skips_bonus(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            first = credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
            )
            _backdate_event(first, days_ago=89)

            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "1"),
            )

        assert not LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.EARN_RETURN
        ).exists()

    def test_bonus_idempotent_on_replay(self, tenant, customer, service):
        # If credit_points for the same booking fires twice (replay), the
        # second call is a no-op for the main event. Long-return bonus
        # also must NOT fire twice.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            first = credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
            )
            _backdate_event(first, days_ago=91)

            booking_2 = _booking(tenant, customer, service, "1")
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking_2,
            )
            # Replay.
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking_2,
            )

        assert (
            LoyaltyEvent.all_tenants.filter(event_type=LoyaltyEvent.EventType.EARN_RETURN).count()
            == 1
        )


class TestReferralCompletion:
    @pytest.fixture
    def referrer(self, tenant) -> BotUser:
        return BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="loy-bonus-referrer"
        )

    def test_first_visit_credits_referrer(self, tenant, customer, service, referrer):
        with tenant_scope(tenant):
            create_referral(referrer=referrer, referee=customer)

            account = get_or_create_account(customer)
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
            )

        referrer_account = LoyaltyAccount.all_tenants.get(customer=referrer)
        assert referrer_account.balance == REFERRAL_BONUS_POINTS
        # Referral row flipped COMPLETED.
        referral = LoyaltyReferral.all_tenants.get(referee_customer=customer)
        assert referral.status == LoyaltyReferral.Status.COMPLETED
        assert referral.completed_at is not None

    def test_second_visit_no_double_credit(self, tenant, customer, service, referrer):
        with tenant_scope(tenant):
            create_referral(referrer=referrer, referee=customer)
            account = get_or_create_account(customer)
            for i in range(2):
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_booking(tenant, customer, service, str(i)),
                )

        referrer_account = LoyaltyAccount.all_tenants.get(customer=referrer)
        assert referrer_account.balance == REFERRAL_BONUS_POINTS  # not 100

    def test_no_pending_referral_silent(self, tenant, customer, service):
        # Customer not referred by anyone — first visit just credits them.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
            )

        # Only one EARN_VISIT, no EARN_REFERRAL anywhere in DB.
        assert not LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.EARN_REFERRAL
        ).exists()

    def test_referrer_opted_out_marks_completed_but_no_credit(
        self, tenant, customer, service, referrer
    ):
        with tenant_scope(tenant):
            referrer_account = get_or_create_account(referrer)
            opt_out(referrer_account)
            create_referral(referrer=referrer, referee=customer)

            account = get_or_create_account(customer)
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
            )

        referrer_account.refresh_from_db()
        assert referrer_account.balance == 0  # opted out, no payout
        # Referral still marked completed (prevents re-attempts).
        referral = LoyaltyReferral.all_tenants.get(referee_customer=customer)
        assert referral.status == LoyaltyReferral.Status.COMPLETED


class TestCreateReferral:
    @pytest.fixture
    def referrer(self, tenant) -> BotUser:
        return BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="cr-referrer"
        )

    def test_create_returns_pending_row(self, tenant, customer, referrer):
        with tenant_scope(tenant):
            referral = create_referral(referrer=referrer, referee=customer)
        assert referral is not None
        assert referral.status == LoyaltyReferral.Status.PENDING

    def test_duplicate_referral_silent(self, tenant, customer, referrer):
        # Per the unique constraint, a second create_referral for the
        # same referee returns None (existing referrer keeps the claim).
        another_referrer = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="cr-another"
        )
        with tenant_scope(tenant):
            first = create_referral(referrer=referrer, referee=customer)
            second = create_referral(referrer=another_referrer, referee=customer)
        assert first is not None
        assert second is None
        # Original referrer wins.
        referral = LoyaltyReferral.all_tenants.get(referee_customer=customer)
        assert referral.referrer_customer_id == referrer.pk

    def test_self_referral_rejected(self, tenant, customer):
        with tenant_scope(tenant), pytest.raises(ValueError, match="self-referral"):
            create_referral(referrer=customer, referee=customer)

    def test_cross_tenant_rejected(self, tenant, customer):
        other_tenant = Tenant.objects.create(slug="loy-bonus-2", name="Other")
        cross_referrer = BotUser.all_tenants.create(
            tenant=other_tenant, channel="max", channel_user_id="cross-ref"
        )
        with tenant_scope(tenant), pytest.raises(ValueError, match="share tenant"):
            create_referral(referrer=cross_referrer, referee=customer)


class TestRevokeFirstVisitClearsReferralClaim:
    """If the customer's first visit gets cancelled-post-completion,
    Phase 1.b revokes the points. The referral should NOT auto-revert
    — we already paid the referrer. Confirm balance state."""

    @pytest.fixture
    def referrer(self, tenant) -> BotUser:
        return BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="rev-referrer"
        )

    def test_revoke_does_not_undo_referral_credit(self, tenant, customer, service, referrer):
        # Earn → referral credited → cancel.
        with tenant_scope(tenant):
            create_referral(referrer=referrer, referee=customer)
            account = get_or_create_account(customer)
            booking = _booking(tenant, customer, service, "0")
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking,
            )
            revoke_visit_points(account, booking=booking)

        # Referee's balance back to 0; referrer keeps the 50.
        account.refresh_from_db()
        assert account.balance == 0
        referrer_account = LoyaltyAccount.all_tenants.get(customer=referrer)
        assert referrer_account.balance == REFERRAL_BONUS_POINTS
        # Referral stays COMPLETED — would need a separate dispute flow
        # to reverse referral payouts (not in Phase 2.d).
        referral = LoyaltyReferral.all_tenants.get(referee_customer=customer)
        assert referral.status == LoyaltyReferral.Status.COMPLETED
