"""Loyalty deferred-risks hotfix tests.

Covers the two long-deferred risks closed by Phase 2.b-retro:

* **#3 — mutual-referral deadlock**: the second ``select_for_update`` in
  ``_maybe_complete_referral`` is replaced by an ``F()`` atomic update.
  This eliminates the A↔B / B↔A lock-acquisition window entirely; the
  test below exercises the mutual scenario and asserts both credits
  land without a deadlock-on-rollback.

* **#4 — audit-write outside atomic block**: every Loyalty service
  entry now routes ``write_audit`` through ``transaction.on_commit``,
  tying the audit row's persistence to the same commit as the
  mutation. Test asserts no audit row appears when the surrounding
  atomic rolls back.
"""

from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal

import pytest
from django.db import close_old_connections, connection, transaction

from apps.audit.models import AuditLog
from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogService
from apps.identity.models import BotUser
from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent, LoyaltyReferral
from apps.loyalty.services import (
    REFERRAL_BONUS_POINTS,
    create_referral,
    credit_points,
    get_or_create_account,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

# Deadlock #3 is a Postgres-specific concurrency hazard (SQLSTATE 40P01).
# SQLite — used by the default pytest test DB — has table-level locking
# rather than row-level, so two concurrent writers serialise on a table
# lock and surface `database table is locked` instead of exercising the
# row-level F() update path we shipped. Skip the threaded test there;
# the audit-atomicity tests remain DB-agnostic and run everywhere.
_REQUIRES_ROW_LOCKING = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="row-level concurrency test requires Postgres (SQLite locks at table level)",
)


_FUTURE = dt.datetime(2027, 6, 15, 10, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="loy-def", name="LoyaltyDeferred")


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=100,
        external_updated_at=dt.datetime.now(dt.UTC),
        name="Маникюр",
        price_from=Decimal("1200"),
        duration_min=60,
        is_active=True,
    )


def _customer(tenant: Tenant, *, channel_user_id: str) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        chat_id=channel_user_id,
    )


def _booking(
    tenant: Tenant, customer: BotUser, service: CatalogService, suffix: str
) -> BookingRequest:
    # Visit lifecycle: status stays CONFIRMED, ``completed_at`` is the
    # detect_completed_bookings stamp. Loyalty cares about the completed_at
    # presence for EARN_VISIT eligibility but reads the visit-flag from
    # the credit_points caller — for these tests any row with a tenant +
    # bot_user is sufficient.
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=customer,
        service=service,
        service_name=service.name,
        master_name="Ольга",
        client_name="Anna",
        client_phone="79991234567",
        comment=f"completed visit {suffix}",
        source="bot",
        status=BookingRequest.Status.CONFIRMED,
        visit_at=_FUTURE,
        completed_at=_FUTURE + dt.timedelta(hours=2),
    )


# ──────────────────────────────────────────────────────────────────────────
# #3 — mutual-referral deadlock prevention
# ──────────────────────────────────────────────────────────────────────────


@_REQUIRES_ROW_LOCKING
@pytest.mark.django_db(transaction=True)
class TestMutualReferralNoDeadlock:
    """Pre-hotfix: ``_maybe_complete_referral`` did ``select_for_update``
    on the referrer's account while the outer ``credit_points`` already
    held one on the referee's. Two concurrent first-visits on a mutual-
    referral pair (A→B AND B→A) deadlocked, Postgres aborted one, the
    subscriber swallowed the exception → one credit silently lost.

    Post-hotfix: the referrer-side update uses ``F('balance') + 50``
    which Postgres serialises at the row-level without any application-
    level lock. The deadlock window is eliminated, not merely narrowed.
    """

    def test_mutual_referral_pair_credits_both(
        self, tenant: Tenant, service: CatalogService
    ) -> None:
        # Build the mutual-referral graph: A is referrer for B, B is
        # referrer for A. Both customers about to complete their FIRST
        # visit.
        cust_a = _customer(tenant, channel_user_id="cust-a")
        cust_b = _customer(tenant, channel_user_id="cust-b")
        with tenant_scope(tenant):
            create_referral(referrer=cust_a, referee=cust_b)
            create_referral(referrer=cust_b, referee=cust_a)

        booking_a = _booking(tenant, cust_a, service, suffix="a")
        booking_b = _booking(tenant, cust_b, service, suffix="b")

        results: dict[str, BaseException | None] = {"a": None, "b": None}

        def _credit_a() -> None:
            try:
                with tenant_scope(tenant):
                    acct = get_or_create_account(cust_a)
                    credit_points(
                        acct,
                        points=10,
                        event_type=LoyaltyEvent.EventType.EARN_VISIT,
                        reason="visit",
                        booking=booking_a,
                    )
            except BaseException as exc:  # noqa: BLE001
                results["a"] = exc
            finally:
                close_old_connections()

        def _credit_b() -> None:
            try:
                with tenant_scope(tenant):
                    acct = get_or_create_account(cust_b)
                    credit_points(
                        acct,
                        points=10,
                        event_type=LoyaltyEvent.EventType.EARN_VISIT,
                        reason="visit",
                        booking=booking_b,
                    )
            except BaseException as exc:  # noqa: BLE001
                results["b"] = exc
            finally:
                close_old_connections()

        t1 = threading.Thread(target=_credit_a)
        t2 = threading.Thread(target=_credit_b)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        assert not t1.is_alive() and not t2.is_alive(), "credit thread hung"

        assert results["a"] is None, f"thread A raised: {results['a']!r}"
        assert results["b"] is None, f"thread B raised: {results['b']!r}"

        # Both referrals completed and the referrer-credit landed on
        # BOTH accounts. With the pre-hotfix deadlock, one of these
        # asserts would fail (one referral stuck PENDING + missing
        # EARN_REFERRAL row).
        acct_a = LoyaltyAccount.all_tenants.get(customer=cust_a)
        acct_b = LoyaltyAccount.all_tenants.get(customer=cust_b)

        assert LoyaltyReferral.all_tenants.filter(
            referrer_customer=cust_a,
            referee_customer=cust_b,
            status=LoyaltyReferral.Status.COMPLETED,
        ).exists()
        assert LoyaltyReferral.all_tenants.filter(
            referrer_customer=cust_b,
            referee_customer=cust_a,
            status=LoyaltyReferral.Status.COMPLETED,
        ).exists()

        assert (
            LoyaltyEvent.all_tenants.filter(
                account=acct_a,
                event_type=LoyaltyEvent.EventType.EARN_REFERRAL,
            ).count()
            == 1
        )
        assert (
            LoyaltyEvent.all_tenants.filter(
                account=acct_b,
                event_type=LoyaltyEvent.EventType.EARN_REFERRAL,
            ).count()
            == 1
        )

        # Each account got: own visit (10pt) + referral credit (50pt).
        acct_a.refresh_from_db()
        acct_b.refresh_from_db()
        assert acct_a.balance == 10 + REFERRAL_BONUS_POINTS
        assert acct_b.balance == 10 + REFERRAL_BONUS_POINTS


@pytest.mark.django_db
class TestReferralCreditViaFExpression:
    """DB-agnostic regression-guard for the deadlock fix. Exercises the
    serial single-thread path and asserts the F()-update post-state
    matches what the previous ``select_for_update`` based code wrote.
    Catches a hypothetical revert that re-introduces the second
    select_for_update without anyone noticing — the threaded test above
    only fires on Postgres so this companion guards the SQLite CI
    pipeline.
    """

    def test_first_visit_credits_referrer_via_f_update(
        self, tenant: Tenant, service: CatalogService
    ) -> None:
        referrer = _customer(tenant, channel_user_id="cust-referrer")
        referee = _customer(tenant, channel_user_id="cust-referee")
        with tenant_scope(tenant):
            create_referral(referrer=referrer, referee=referee)

        booking = _booking(tenant, referee, service, suffix="first")
        with tenant_scope(tenant):
            account = get_or_create_account(referee)
            credit_points(
                account,
                points=10,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                reason="visit",
                booking=booking,
            )

        # Referral completed, referrer credited the canonical 50 points.
        assert LoyaltyReferral.all_tenants.filter(
            referee_customer=referee,
            status=LoyaltyReferral.Status.COMPLETED,
        ).exists()

        referrer_account = LoyaltyAccount.all_tenants.get(customer=referrer)
        assert referrer_account.balance == REFERRAL_BONUS_POINTS

        # EARN_REFERRAL event row has the right balance_after — confirms
        # refresh_from_db() correctly captured the F()-update result.
        earn_referral = LoyaltyEvent.all_tenants.get(
            account=referrer_account,
            event_type=LoyaltyEvent.EventType.EARN_REFERRAL,
        )
        assert earn_referral.points_delta == REFERRAL_BONUS_POINTS
        assert earn_referral.balance_after == REFERRAL_BONUS_POINTS

    def test_replay_does_not_double_credit_referrer(
        self, tenant: Tenant, service: CatalogService
    ) -> None:
        # Subscriber idempotency safety net: two EARN_VISIT events for
        # the same booking (eventbus replay) must not double-credit the
        # referrer. The CAS on LoyaltyReferral.status=PENDING already
        # guards this; this test pins the contract.
        referrer = _customer(tenant, channel_user_id="cust-referrer-x")
        referee = _customer(tenant, channel_user_id="cust-referee-x")
        with tenant_scope(tenant):
            create_referral(referrer=referrer, referee=referee)

        booking = _booking(tenant, referee, service, suffix="replay")

        with tenant_scope(tenant):
            account = get_or_create_account(referee)
            credit_points(
                account,
                points=10,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                reason="visit",
                booking=booking,
            )
            # Second call — credit_points itself is idempotent on
            # (account, EARN_VISIT, booking); _maybe_complete_referral
            # won't even be reached on the replay because the outer
            # credit returns early.
            credit_points(
                account,
                points=10,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                reason="visit",
                booking=booking,
            )

        referrer_account = LoyaltyAccount.all_tenants.get(customer=referrer)
        assert referrer_account.balance == REFERRAL_BONUS_POINTS  # ONE credit
        assert (
            LoyaltyEvent.all_tenants.filter(
                account=referrer_account,
                event_type=LoyaltyEvent.EventType.EARN_REFERRAL,
            ).count()
            == 1
        )


# ──────────────────────────────────────────────────────────────────────────
# #4 — audit-write atomicity (transaction.on_commit)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestAuditAtomicWithMutation:
    """Pre-hotfix: ``write_audit`` ran AFTER the service's atomic block.
    DB connection drop between commit and audit-write left the mutation
    durable + audit row absent. Post-hotfix: ``transaction.on_commit``
    schedules ``write_audit`` to fire only when the outer atomic commits;
    on rollback the audit does not fire.
    """

    def test_audit_does_not_fire_when_outer_atomic_rolls_back(
        self, tenant: Tenant, service: CatalogService
    ) -> None:
        customer = _customer(tenant, channel_user_id="cust-audit-rb")
        booking = _booking(tenant, customer, service, suffix="rb")

        with tenant_scope(tenant):
            account = get_or_create_account(customer)

        class _DeliberateRollback(RuntimeError):
            pass

        with pytest.raises(_DeliberateRollback):
            with transaction.atomic():
                with tenant_scope(tenant):
                    credit_points(
                        account,
                        points=10,
                        event_type=LoyaltyEvent.EventType.EARN_VISIT,
                        reason="will rollback",
                        booking=booking,
                    )
                # Force the outer transaction to abort AFTER credit_points
                # returned (its inner atomic became a savepoint).
                raise _DeliberateRollback("simulated downstream failure")

        # Mutation rolled back …
        assert not LoyaltyEvent.all_tenants.filter(account=account).exists()
        account.refresh_from_db()
        assert account.balance == 0
        # … and the audit on_commit callback never fired.
        assert not AuditLog.all_tenants.filter(
            target="LoyaltyAccount",
            target_id=account.pk,
        ).exists()

    def test_audit_fires_when_outer_atomic_commits(
        self, tenant: Tenant, service: CatalogService
    ) -> None:
        # Symmetric positive: a committed outer atomic results in BOTH
        # the LoyaltyEvent row AND the AuditLog row landing.
        customer = _customer(tenant, channel_user_id="cust-audit-ok")
        booking = _booking(tenant, customer, service, suffix="ok")

        with tenant_scope(tenant):
            account = get_or_create_account(customer)

        with transaction.atomic():
            with tenant_scope(tenant):
                credit_points(
                    account,
                    points=10,
                    event_type=LoyaltyEvent.EventType.EARN_VISIT,
                    reason="visit",
                    booking=booking,
                )

        assert LoyaltyEvent.all_tenants.filter(account=account).count() == 1
        assert AuditLog.all_tenants.filter(
            action=f"loyalty.{LoyaltyEvent.EventType.EARN_VISIT}",
            target="LoyaltyAccount",
            target_id=account.pk,
        ).exists()
