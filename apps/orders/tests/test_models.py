"""Order + PaymentEvent model tests (DRF-843 / Phase 1 / B7).

Covers:

* Tenant-scoped manager — Tenant A's bot_user can't see Tenant B's
  orders via the default ``objects`` manager.
* PaymentEvent.external_event_id UNIQUE constraint — re-inserting the
  same event id raises IntegrityError.
* Order defaults (status=pending, kind=certificate) match the
  documented contract.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.identity.models import BotUser
from apps.orders.models import Order, PaymentEvent
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_a(db) -> Tenant:
    return Tenant.objects.create(slug="tenant-a", name="A")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="tenant-b", name="B")


@pytest.fixture
def user_a(tenant_a: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_a,
        channel="max",
        channel_user_id="user-a",
        chat_id="chat-a",
        phone="",
        client_name="Alice",
    )


@pytest.fixture
def user_b(tenant_b: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_b,
        channel="max",
        channel_user_id="user-b",
        chat_id="chat-b",
        phone="",
        client_name="Bob",
    )


class TestOrderDefaults:
    def test_pending_is_default_status(self, tenant_a: Tenant, user_a: BotUser) -> None:
        with tenant_scope(tenant_a):
            o = Order.objects.create(
                tenant=tenant_a,
                bot_user=user_a,
                amount_rub=Decimal("1000"),
            )
            assert o.status == Order.Status.PENDING
            assert o.kind == Order.Kind.CERTIFICATE
            assert o.paid_at is None
            assert o.external_payment_id == ""
            assert o.checkout_url == ""


class TestTenantIsolation:
    def test_objects_manager_filters_by_current_tenant(
        self,
        tenant_a: Tenant,
        tenant_b: Tenant,
        user_a: BotUser,
        user_b: BotUser,
    ) -> None:
        Order.all_tenants.create(
            tenant=tenant_a,
            bot_user=user_a,
            amount_rub=Decimal("1000"),
        )
        Order.all_tenants.create(
            tenant=tenant_b,
            bot_user=user_b,
            amount_rub=Decimal("2000"),
        )

        with tenant_scope(tenant_a):
            ids_a = set(Order.objects.values_list("amount_rub", flat=True))
        with tenant_scope(tenant_b):
            ids_b = set(Order.objects.values_list("amount_rub", flat=True))

        assert ids_a == {Decimal("1000.00")}
        assert ids_b == {Decimal("2000.00")}
        # all_tenants escape-hatch sees both.
        assert Order.all_tenants.count() == 2


class TestPaymentEventUnique:
    def test_duplicate_external_event_id_rejected(self) -> None:
        PaymentEvent.objects.create(
            external_event_id="evt-unique-1",
            event_type="payment.succeeded",
            raw_payload={"event": "payment.succeeded"},
        )
        with pytest.raises(IntegrityError):
            PaymentEvent.objects.create(
                external_event_id="evt-unique-1",
                event_type="payment.succeeded",
                raw_payload={"event": "payment.succeeded"},
            )

    def test_get_or_create_idempotency(self) -> None:
        row1, c1 = PaymentEvent.objects.get_or_create(
            external_event_id="evt-idem-1",
            defaults={
                "event_type": "payment.succeeded",
                "raw_payload": {},
            },
        )
        assert c1 is True
        row2, c2 = PaymentEvent.objects.get_or_create(
            external_event_id="evt-idem-1",
            defaults={"event_type": "payment.succeeded", "raw_payload": {}},
        )
        assert c2 is False
        assert row2.pk == row1.pk

    def test_paymentevent_str(self) -> None:
        row = PaymentEvent.objects.create(
            external_event_id="evt-str",
            event_type="payment.succeeded",
            raw_payload={},
        )
        s = str(row)
        assert "payment.succeeded" in s
        assert "evt-str" in s
