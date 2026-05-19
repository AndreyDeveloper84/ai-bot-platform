"""Phase 2.a — tier recompute on credit/revoke."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogService
from apps.eventbus.models import DomainEvent
from apps.eventbus.vocabulary import CUSTOMER_TIER_CHANGED
from apps.identity.models import BotUser
from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent
from apps.loyalty.services import (
    TIER_FAVORITE_VISIT_THRESHOLD,
    TIER_REGULAR_VISIT_THRESHOLD,
    credit_points,
    get_or_create_account,
    recompute_tier,
    revoke_visit_points,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


_FUTURE = dt.datetime(2027, 6, 15, 10, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="loy-tier", name="LoyaltyTier")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="loy-tier-bot")


@pytest.fixture
def service(tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=99,
        external_updated_at=dt.datetime.now(dt.UTC),
        name="Маникюр",
        price_from=Decimal("1200"),
        duration_min=60,
        is_active=True,
    )


def _make_booking(tenant, customer, service, suffix: str) -> BookingRequest:
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


class TestTierThresholdsOnCredit:
    def test_first_visits_keep_starter(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(TIER_REGULAR_VISIT_THRESHOLD - 1):  # 3 visits
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_make_booking(tenant, customer, service, str(i)),
                )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER
        assert account.tier_changed_at is None
        # No TIER_CHANGED events written.
        assert not LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.TIER_CHANGED
        ).exists()

    def test_fourth_visit_crosses_to_regular(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(TIER_REGULAR_VISIT_THRESHOLD):  # 4 visits
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_make_booking(tenant, customer, service, str(i)),
                )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.REGULAR
        assert account.tier_changed_at is not None
        # Exactly ONE TIER_CHANGED row (4th crossing) — not one per visit.
        tier_events = LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.TIER_CHANGED
        )
        assert tier_events.count() == 1
        assert tier_events.first().metadata["new_tier"] == "regular"
        assert tier_events.first().metadata["old_tier"] == "starter"

    def test_twelfth_visit_crosses_to_favorite(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(TIER_FAVORITE_VISIT_THRESHOLD):  # 12 visits
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_make_booking(tenant, customer, service, str(i)),
                )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.FAVORITE
        # Two TIER_CHANGED rows: starter→regular (4th), regular→favorite (12th).
        tier_events = list(
            LoyaltyEvent.all_tenants.filter(
                event_type=LoyaltyEvent.EventType.TIER_CHANGED
            ).order_by("occurred_at")
        )
        assert len(tier_events) == 2
        assert tier_events[0].metadata["new_tier"] == "regular"
        assert tier_events[1].metadata["new_tier"] == "favorite"


class TestTierEventBusEmit:
    def test_customer_tier_changed_envelope_emitted(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(TIER_REGULAR_VISIT_THRESHOLD):
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_make_booking(tenant, customer, service, str(i)),
                )

        envelopes = DomainEvent.objects.filter(event_name=CUSTOMER_TIER_CHANGED)
        assert envelopes.count() == 1
        env = envelopes.first()
        assert env.data["customer_id"] == str(customer.id)
        assert env.data["old_tier"] == "starter"
        assert env.data["new_tier"] == "regular"
        assert "visits=4" in env.data["reason"]
        assert env.tenant_id == tenant.id

    def test_no_emit_on_intermediate_credits(self, tenant, customer, service):
        # Visits 1, 2, 3 don't change tier → no customer.tier.changed.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(3):
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_make_booking(tenant, customer, service, str(i)),
                )

        assert not DomainEvent.objects.filter(event_name=CUSTOMER_TIER_CHANGED).exists()


class TestTierDowngradeOnRevoke:
    def test_revoke_after_threshold_downgrades(self, tenant, customer, service):
        # Earn 4 visits → regular. Revoke 1 → back to starter.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            bookings = [_make_booking(tenant, customer, service, str(i)) for i in range(4)]
            for b in bookings:
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=b,
                )

            account.refresh_from_db()
            assert account.tier == LoyaltyAccount.Tier.REGULAR

            # Cancel one. Effective visits = 4 - 1 = 3 → starter.
            revoke_visit_points(account, booking=bookings[0])

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER
        # Two TIER_CHANGED rows total: up to regular, down to starter.
        tier_events = list(
            LoyaltyEvent.all_tenants.filter(
                event_type=LoyaltyEvent.EventType.TIER_CHANGED
            ).order_by("occurred_at")
        )
        assert len(tier_events) == 2
        assert tier_events[1].metadata["old_tier"] == "regular"
        assert tier_events[1].metadata["new_tier"] == "starter"

    def test_revoke_below_threshold_no_op(self, tenant, customer, service):
        # 3 visits, then revoke 1 → still starter. No TIER_CHANGED emitted.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            bookings = [_make_booking(tenant, customer, service, str(i)) for i in range(3)]
            for b in bookings:
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=b,
                )
            revoke_visit_points(account, booking=bookings[0])

        assert not LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.TIER_CHANGED
        ).exists()


class TestRecomputeIdempotency:
    def test_recompute_with_no_change_returns_none(self, tenant, customer):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            # No EARN_VISIT events → effective count 0 → tier stays starter.
            result = recompute_tier(account)
        assert result is None

    def test_recompute_after_change_is_noop(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(TIER_REGULAR_VISIT_THRESHOLD):
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_make_booking(tenant, customer, service, str(i)),
                )
            account.refresh_from_db()
            # Second recompute on same data → no change.
            result = recompute_tier(account)
        assert result is None


class TestManualAdjustDoesNotAffectTier:
    def test_manual_adjust_no_tier_recompute(self, tenant, customer):
        # MANUAL_ADJUST is admin goodwill points — not visit progress.
        # Tier should not move from a single manual_adjust call.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            credit_points(
                account,
                points=1000,
                event_type=LoyaltyEvent.EventType.MANUAL_ADJUST,
                reason="admin goodwill",
            )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER
        assert not LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.TIER_CHANGED
        ).exists()
