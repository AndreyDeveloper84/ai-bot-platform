"""Phase 2.b — tier multipliers + LTV thresholds."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogService
from apps.identity.models import BotUser
from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent
from apps.loyalty.services import (
    LONG_RETURN_BONUS_POINTS,
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
    return Tenant.objects.create(slug="loy-2b", name="Loyalty2b")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="loy-2b-bot")


@pytest.fixture
def service(tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=300,
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


def _backdate(event: LoyaltyEvent, days_ago: int) -> None:
    LoyaltyEvent.all_tenants.filter(pk=event.pk).update(
        occurred_at=timezone.now() - dt.timedelta(days=days_ago)
    )


# ── Long-return multiplier ─────────────────────────────────────────────


def _trigger_long_return(tenant, customer, service):
    """Set up: 91-day gap between two visits."""
    with tenant_scope(tenant):
        account = get_or_create_account(customer)
        first = credit_points(
            account,
            points=12,
            event_type=LoyaltyEvent.EventType.EARN_VISIT,
            booking=_booking(tenant, customer, service, "0"),
        )
    _backdate(first, days_ago=91)
    return account


class TestLongReturnMultiplier:
    def test_starter_tier_gets_1x_bonus(self, tenant, customer, service):
        account = _trigger_long_return(tenant, customer, service)
        # Confirm tier is still starter (1 visit only).
        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER

        with tenant_scope(tenant):
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "1"),
            )

        bonus = LoyaltyEvent.all_tenants.get(event_type=LoyaltyEvent.EventType.EARN_RETURN)
        assert bonus.points_delta == LONG_RETURN_BONUS_POINTS  # 30
        assert bonus.metadata["multiplier"] == 1
        assert bonus.metadata["tier_at_credit"] == "starter"

    def test_regular_tier_gets_2x_bonus(self, tenant, customer, service):
        # Earn 4 visits → regular tier. Then long-return triggers.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(4):
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_booking(tenant, customer, service, str(i)),
                    metadata={"service_price_rub": 1200},
                )

        # All 4 visits older than 91d. Customer returns.
        for ev in LoyaltyEvent.all_tenants.filter(
            account=account, event_type=LoyaltyEvent.EventType.EARN_VISIT
        ):
            _backdate(ev, days_ago=91)

        with tenant_scope(tenant):
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "10"),
                metadata={"service_price_rub": 1200},
            )

        bonus = LoyaltyEvent.all_tenants.get(event_type=LoyaltyEvent.EventType.EARN_RETURN)
        assert bonus.points_delta == LONG_RETURN_BONUS_POINTS * 2  # 60
        assert bonus.metadata["multiplier"] == 2
        assert bonus.metadata["tier_at_credit"] == "regular"

    def test_favorite_tier_gets_3x_bonus(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            # 12 visits to reach favorite.
            for i in range(12):
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_booking(tenant, customer, service, str(i)),
                    metadata={"service_price_rub": 1200},
                )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.FAVORITE

        for ev in LoyaltyEvent.all_tenants.filter(
            account=account, event_type=LoyaltyEvent.EventType.EARN_VISIT
        ):
            _backdate(ev, days_ago=91)

        with tenant_scope(tenant):
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "20"),
                metadata={"service_price_rub": 1200},
            )

        bonus = LoyaltyEvent.all_tenants.get(event_type=LoyaltyEvent.EventType.EARN_RETURN)
        assert bonus.points_delta == LONG_RETURN_BONUS_POINTS * 3  # 90
        assert bonus.metadata["multiplier"] == 3
        assert bonus.metadata["tier_at_credit"] == "favorite"


# ── LTV thresholds ─────────────────────────────────────────────────────


class TestLtvThresholds:
    def test_single_big_visit_crosses_regular_via_ltv(self, tenant, customer, service):
        # ONE visit at 8500₽ → meets LTV threshold (≥8000) without
        # needing 4 visits.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            credit_points(
                account,
                points=85,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
                metadata={"service_price_rub": 8500},
            )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.REGULAR

    def test_one_premium_visit_crosses_favorite_via_ltv(self, tenant, customer, service):
        # ONE visit at 35000₽ (e.g. expensive procedure) → favorite.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            credit_points(
                account,
                points=350,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
                metadata={"service_price_rub": 35000},
            )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.FAVORITE

    def test_ltv_below_threshold_stays_starter(self, tenant, customer, service):
        # 3 visits × 1500₽ = 4500₽ — below both visit count (4) and LTV (8000).
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(3):
                credit_points(
                    account,
                    points=15,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_booking(tenant, customer, service, str(i)),
                    metadata={"service_price_rub": 1500},
                )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER

    def test_tier_changed_metadata_carries_ltv(self, tenant, customer, service):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            credit_points(
                account,
                points=85,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
                metadata={"service_price_rub": 8500},
            )

        tier_event = LoyaltyEvent.all_tenants.get(event_type=LoyaltyEvent.EventType.TIER_CHANGED)
        assert tier_event.metadata["ltv_rub"] == 8500
        assert tier_event.metadata["visit_count"] == 1


class TestLtvOnRevoke:
    def test_revoke_reduces_ltv(self, tenant, customer, service):
        # Customer hits regular via LTV (one 8500₽ visit). Revoke → LTV
        # drops to 0 → starter.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            booking = _booking(tenant, customer, service, "0")
            credit_points(
                account,
                points=85,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking,
                metadata={"service_price_rub": 8500},
            )
            account.refresh_from_db()
            assert account.tier == LoyaltyAccount.Tier.REGULAR

            revoke_visit_points(account, booking=booking)

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER

    def test_revoke_propagates_service_price_rub(self, tenant, customer, service):
        # Confirms the REFUND_REVOKE event's metadata carries the price.
        # This is the mechanism that lets _effective_metrics correctly
        # subtract LTV.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            booking = _booking(tenant, customer, service, "0")
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking,
                metadata={"service_price_rub": 1200},
            )
            revoke_visit_points(account, booking=booking)

        revoke = LoyaltyEvent.all_tenants.get(event_type=LoyaltyEvent.EventType.REFUND_REVOKE)
        assert revoke.metadata["service_price_rub"] == 1200


class TestVisitCountThresholdStillWorks:
    def test_4_visits_no_ltv_metadata_still_regular(self, tenant, customer, service):
        # Legacy path: visits without service_price_rub metadata. LTV=0,
        # but visit count = 4 → regular via OR.
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            for i in range(4):
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_booking(tenant, customer, service, str(i)),
                )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.REGULAR


class TestRecomputeTierIdempotent:
    def test_post_credit_recompute_returns_none(self, tenant, customer, service):
        # After credit_points already triggered recompute, calling
        # recompute_tier directly should be a no-op (same tier).
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            credit_points(
                account,
                points=85,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "0"),
                metadata={"service_price_rub": 8500},
            )
            account.refresh_from_db()
            result = recompute_tier(account)
        assert result is None
