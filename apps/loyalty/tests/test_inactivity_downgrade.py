"""Phase 2.c — inactivity hard-downgrade beat."""

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
    INACTIVITY_HARD_DOWNGRADE_DAYS,
    TIER_REGULAR_VISIT_THRESHOLD,
    apply_inactivity_downgrades,
    credit_points,
    get_or_create_account,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


_FUTURE = dt.datetime(2027, 6, 15, 10, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="loy-inact", name="LoyaltyInactivity")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="loy-inact-bot")


@pytest.fixture
def service(tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=200,
        external_updated_at=dt.datetime.now(dt.UTC),
        name="Маникюр",
        price_from=Decimal("1200"),
        duration_min=60,
        is_active=True,
    )


def _booking(tenant, customer, service, suffix) -> BookingRequest:
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


def _earn_regular_tier(tenant, customer, service):
    """Set up: account exists at REGULAR tier (4 EARN_VISIT events)."""
    with tenant_scope(tenant):
        account = get_or_create_account(customer)
        for i in range(TIER_REGULAR_VISIT_THRESHOLD):
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, str(i)),
            )
    account.refresh_from_db()
    assert account.tier == LoyaltyAccount.Tier.REGULAR
    return account


def _backdate_all_visits(account, days_ago: int) -> None:
    """Force every EARN_VISIT row's occurred_at into the past."""
    LoyaltyEvent.all_tenants.filter(
        account=account, event_type=LoyaltyEvent.EventType.EARN_VISIT
    ).update(occurred_at=timezone.now() - dt.timedelta(days=days_ago))


class TestHardDowngradeSelection:
    def test_11_month_inactive_no_downgrade(self, tenant, customer, service):
        account = _earn_regular_tier(tenant, customer, service)
        _backdate_all_visits(account, days_ago=330)

        result = apply_inactivity_downgrades()

        assert result["downgraded"] == 0
        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.REGULAR

    def test_13_month_inactive_downgrades_to_starter(self, tenant, customer, service):
        account = _earn_regular_tier(tenant, customer, service)
        _backdate_all_visits(account, days_ago=400)

        result = apply_inactivity_downgrades()

        assert result["downgraded"] == 1
        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER
        assert account.tier_reset_at is not None

    def test_starter_already_excluded(self, tenant, customer):
        # Customer with zero visits, default starter tier — never selected.
        with tenant_scope(tenant):
            get_or_create_account(customer)

        result = apply_inactivity_downgrades()

        assert result["scanned"] == 0
        assert result["downgraded"] == 0

    def test_active_customer_not_downgraded(self, tenant, customer, service):
        # Recent visits — NOT a candidate even though tier_reset_at is NULL.
        account = _earn_regular_tier(tenant, customer, service)
        # Don't backdate. Recent EARN_VISIT events.

        result = apply_inactivity_downgrades()

        assert result["downgraded"] == 0
        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.REGULAR


class TestPostDowngradeBehavior:
    def test_returning_customer_climbs_fresh(self, tenant, customer, service):
        account = _earn_regular_tier(tenant, customer, service)
        _backdate_all_visits(account, days_ago=400)
        apply_inactivity_downgrades()
        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER

        # Customer returns — 1 new visit. Historic 4 visits should NOT
        # bounce them back to REGULAR; only NEW visits count.
        with tenant_scope(tenant):
            credit_points(
                account,
                points=12,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                booking=_booking(tenant, customer, service, "100"),
            )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.STARTER

    def test_returning_customer_reaches_regular_after_4_fresh(self, tenant, customer, service):
        account = _earn_regular_tier(tenant, customer, service)
        _backdate_all_visits(account, days_ago=400)
        apply_inactivity_downgrades()

        with tenant_scope(tenant):
            for i in range(TIER_REGULAR_VISIT_THRESHOLD):
                # Suffixes 10..13 — keeps _booking's int(suffix) parse happy
                # while ensuring distinct booking primary keys.
                credit_points(
                    account,
                    points=12,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    booking=_booking(tenant, customer, service, str(10 + i)),
                )

        account.refresh_from_db()
        assert account.tier == LoyaltyAccount.Tier.REGULAR


class TestIdempotency:
    def test_second_beat_run_is_noop(self, tenant, customer, service):
        account = _earn_regular_tier(tenant, customer, service)
        _backdate_all_visits(account, days_ago=400)

        first = apply_inactivity_downgrades()
        second = apply_inactivity_downgrades()

        assert first["downgraded"] == 1
        assert second["scanned"] == 0  # account now STARTER, excluded


def _rows_added_since(qs, existed: set) -> list:
    """Строки ``qs``, которых нет в ``existed`` — по признаку, не по положению (DRF-2649).

    «Добавленная действием» и «последняя по ``occurred_at``» — разные
    утверждения. Здесь до понижения уже есть строка того же рода:
    ``_earn_regular_tier`` сам поднимает счёт до REGULAR и пишет
    ``TIER_CHANGED`` / ``customer.tier.changed`` starter→regular (замер: 1 до,
    2 после). Ключи отбору не помогают: ``LoyaltyEvent.id`` — uuid4,
    ``DomainEvent.event_id`` — ULID, растущий лишь с точностью до
    миллисекунды; и даже растущий ключ отвечал бы «последняя», а не
    «добавленная».
    """
    return list(qs.exclude(pk__in=existed))


def _move_later(qs) -> None:
    """Приманка: уже существующие строки — на час ПОЗЖЕ любой новой.

    Бизнес-логику не трогает: понижение читает визиты, а не прежние смены
    уровня. Отбор «последняя по времени» взял бы приманку детерминированно.
    """
    qs.update(occurred_at=timezone.now() + dt.timedelta(hours=1))


class TestNewRowsAreChosenByIdentity:
    """DRF-2649 — пара к узлам ниже: без понижения отбор пуст, хотя строка
    того же рода (starter→regular) в журнале есть и сдвинута «позже»."""

    def test_nothing_added_means_nothing_returned(self, tenant, customer, service):
        account = _earn_regular_tier(tenant, customer, service)
        changes = LoyaltyEvent.all_tenants.filter(
            account=account, event_type=LoyaltyEvent.EventType.TIER_CHANGED
        )
        existed = set(changes.values_list("pk", flat=True))
        assert len(existed) == 1  # presence: the promotion row is there
        _move_later(changes)

        assert _rows_added_since(changes, existed) == []


class TestTierChangedMetadata:
    def test_tier_changed_carries_inactivity_trigger(self, tenant, customer, service):
        account = _earn_regular_tier(tenant, customer, service)
        _backdate_all_visits(account, days_ago=400)
        changes = LoyaltyEvent.all_tenants.filter(
            account=account, event_type=LoyaltyEvent.EventType.TIER_CHANGED
        )
        existed = set(changes.values_list("pk", flat=True))
        assert len(existed) == 1  # presence: the promotion already left its row
        _move_later(changes)
        apply_inactivity_downgrades()

        (latest,) = _rows_added_since(changes, existed)
        assert latest.metadata["trigger"] == "inactivity_hard_downgrade"
        assert latest.metadata["cutoff_days"] == INACTIVITY_HARD_DOWNGRADE_DAYS
        assert latest.metadata["old_tier"] == "regular"
        assert latest.metadata["new_tier"] == "starter"


class TestSingleEmit:
    """Hotfix B retro review #5: confirm inactivity downgrade emits
    customer.tier.changed EXACTLY ONCE per account transition.

    Before Hotfix B, both `recompute_tier` and the outer task emitted,
    so subscribers saw 2 envelopes per flip — UI would show «вы стали
    Стартовым» twice.
    """

    def test_exactly_one_customer_tier_changed_per_account(self, tenant, customer, service):
        from apps.eventbus.models import DomainEvent

        account = _earn_regular_tier(tenant, customer, service)
        _backdate_all_visits(account, days_ago=400)

        before = DomainEvent.objects.filter(event_name="customer.tier.changed").count()
        apply_inactivity_downgrades()
        after = DomainEvent.objects.filter(event_name="customer.tier.changed").count()

        # +1 per account flipped, not +2.
        assert after - before == 1

    def test_envelope_carries_inactivity_reason(self, tenant, customer, service):
        from apps.eventbus.models import DomainEvent

        account = _earn_regular_tier(tenant, customer, service)
        _backdate_all_visits(account, days_ago=400)
        envelopes = DomainEvent.objects.filter(event_name="customer.tier.changed")
        existed = set(envelopes.values_list("pk", flat=True))
        assert len(existed) == 1  # presence: the promotion already emitted its envelope
        _move_later(envelopes)
        apply_inactivity_downgrades()

        (ev,) = _rows_added_since(envelopes, existed)
        assert ev.data["reason"] == "inactivity_hard_downgrade"
        assert ev.data["old_tier"] == "regular"
        assert ev.data["new_tier"] == "starter"
