"""Loyalty service-layer tests."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogService
from apps.identity.models import BotUser
from apps.loyalty.models import LoyaltyEvent
from apps.loyalty.services import (
    MIN_REDEMPTION_POINTS,
    credit_points,
    get_or_create_account,
    opt_out,
    redeem_points,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


_FUTURE = dt.datetime(2027, 6, 15, 10, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="loy-svc", name="Loyalty Service")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="loy-svc-bot")


@pytest.fixture
def service(tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1,
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
        booking_source="external",  # Bypass attribution validator
    )


class TestGetOrCreateAccount:
    def test_creates_new_account_with_zero_balance(self, tenant, customer, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
        assert account.balance == 0
        assert account.enrolled is True
        assert account.opted_out_at is None

    def test_returns_existing_account_on_second_call(self, tenant, customer, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            a1 = get_or_create_account(customer)
            a2 = get_or_create_account(customer)
        assert a1.pk == a2.pk

    def test_requires_tenant_scope(self, customer):
        with pytest.raises(ValueError, match="tenant"):
            get_or_create_account(customer)


class TestCreditPoints:
    def test_happy_path_increments_balance(self, tenant, customer, booking, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            event = credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking,
                reason="visit",
            )
        assert event is not None
        assert event.points_delta == 12
        assert event.balance_after == 12
        account.refresh_from_db()
        assert account.balance == 12

    def test_idempotent_replay_for_earn_visit(self, tenant, customer, booking, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            first = credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking,
                reason="visit",
            )
            second = credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking,
                reason="visit replay",
            )
        assert first is not None
        assert second is None  # idempotent no-op
        account.refresh_from_db()
        assert account.balance == 12  # not doubled

    def test_opt_out_skips_credit(self, tenant, customer, booking, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            opt_out(account)
            result = credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=booking,
            )
        assert result is None
        account.refresh_from_db()
        assert account.balance == 0

    def test_negative_points_rejected(self, tenant, customer, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            with pytest.raises(ValueError, match="positive"):
                credit_points(account, points=-5, event_type=LoyaltyEvent.EventType.EARN_VISIT)


class TestRedeemPoints:
    def _seeded_account(self, tenant, customer, booking, balance):
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            credit_points(
                account,
                points=balance,
                event_type=LoyaltyEvent.EventType.MANUAL_ADJUST,
                reason="seed",
            )
            account.refresh_from_db()
            return account

    def test_happy_redemption_within_bounds(self, tenant, customer, booking, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        account = self._seeded_account(tenant, customer, booking, balance=500)
        with tenant_scope(tenant):
            event = redeem_points(account, points=100, visit_price_rub=1200, booking=booking)
        assert event.points_delta == -100
        assert event.balance_after == 400
        account.refresh_from_db()
        assert account.balance == 400

    def test_below_minimum_rejected(self, tenant, customer, booking, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        account = self._seeded_account(tenant, customer, booking, balance=100)
        with tenant_scope(tenant), pytest.raises(ValueError, match="minimum redemption"):
            redeem_points(
                account,
                points=MIN_REDEMPTION_POINTS - 1,
                visit_price_rub=1200,
                booking=booking,
            )

    def test_cap_30_percent_enforced(self, tenant, customer, booking, settings):
        # 30% of 1000 ₽ = 300 — even with 9000 balance can only redeem 300.
        settings.STRICT_TENANT_SCOPE = "strict"
        account = self._seeded_account(tenant, customer, booking, balance=9000)
        with tenant_scope(tenant), pytest.raises(ValueError, match="capped"):
            redeem_points(account, points=500, visit_price_rub=1000, booking=booking)

    def test_insufficient_balance_rejected(self, tenant, customer, booking, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        account = self._seeded_account(tenant, customer, booking, balance=200)
        with tenant_scope(tenant), pytest.raises(ValueError, match="insufficient balance"):
            redeem_points(account, points=300, visit_price_rub=5000, booking=booking)

    def test_opt_out_rejects_redemption(self, tenant, customer, booking, settings):
        # Q-L12 says retained balance is REDEEMABLE after opt-out. But the
        # MVP service path returns ValueError; UX shows balance as visible
        # but greyed out. The full Q-L12 path lands in PR-Loyalty-2 with
        # the redemption checkout flow. Documenting current behavior.
        settings.STRICT_TENANT_SCOPE = "strict"
        account = self._seeded_account(tenant, customer, booking, balance=500)
        with tenant_scope(tenant):
            opt_out(account)
            with pytest.raises(ValueError, match="opted out"):
                redeem_points(account, points=100, visit_price_rub=1200, booking=booking)


class TestOptOut:
    def test_stamps_opted_out_at_and_disables(self, tenant, customer, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            opt_out(account)
        account.refresh_from_db()
        assert account.opted_out_at is not None
        assert account.enrolled is False

    def test_second_call_idempotent(self, tenant, customer, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            account = get_or_create_account(customer)
            opt_out(account)
            first_at = account.opted_out_at
            opt_out(account)
        account.refresh_from_db()
        assert account.opted_out_at == first_at  # unchanged
