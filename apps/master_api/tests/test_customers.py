"""Tests for the GET /api/v1/master/customers read-only roster endpoint.

Covers (Phase 1 Tier 2):

* Empty roster — master with no bookings → 200 + empty list.
* Roster ordering — last_visit_at DESC.
* No customer phone in the payload, in any form (DRF-1360 / OD-W2-2).
* Cross-tenant isolation — sibling tenant's customers must NOT leak.
* Counting rules — CONFIRMED + RESCHEDULED counted, CANCELLED excluded.
* Returning + at-risk flags.
* GDPR scrub edge case — booking row with NULL bot_user is skipped.
* Schema completeness — every documented key always present.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import Client
from django.urls import reverse

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.master_api.services import customers as cs
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")


def _utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=MSK)
    return dt_local.astimezone(timezone.utc)


def _make_booking(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    bot_user: BotUser | None,
    visit_local: datetime,
    duration_min: int = 60,
    status: str = BookingRequest.Status.CONFIRMED,
    service_name: str = "маникюр гель-лак",
    client_name: str = "Клиент",
) -> BookingRequest:
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        master=master,
        bot_user=bot_user,
        service_name=service_name,
        client_name=client_name,
        client_phone="+79000000000",
        visit_at=_utc(visit_local),
        duration_min=duration_min,
        status=status,
    )


def _make_client(
    *,
    tenant: Tenant,
    channel_user_id: str,
    client_name: str = "",
    display_name: str = "",
    phone: str = "",
) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name=display_name,
        client_name=client_name,
        chat_id=channel_user_id,
        phone=phone,
    )


# --- endpoint auth gate ---------------------------------------------------


class TestCustomersAuth:
    def test_no_linked_master_returns_401(self, client: Client, bot_user: BotUser) -> None:
        resp = client.get(
            reverse("master_api:customers_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"

    def test_accepted_master_returns_200(
        self, client: Client, bot_user: BotUser, accepted_master: CatalogMaster
    ) -> None:
        resp = client.get(
            reverse("master_api:customers_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json() == {"customers": []}


# --- service-layer aggregation -------------------------------------------


class TestRosterAggregation:
    def test_empty_master_returns_empty_list(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        out = cs.list_master_customers(master=accepted_master)
        assert out == []

    def test_single_customer_one_visit(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _make_client(
            tenant=tenant,
            channel_user_id="cust-1",
            client_name="Анна Петрова",
            phone="+79161234567",
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=anna,
            visit_local=datetime(2026, 5, 20, 14, 0),
        )
        out = cs.list_master_customers(master=accepted_master)
        assert len(out) == 1
        row = out[0]
        # Schema completeness — every documented key present.
        assert set(row.keys()) == {
            "bot_user_id",
            "first_name",
            "last_visit_at",
            "last_visit_service_name",
            "total_visits",
            "is_returning",
            "at_risk",
        }
        assert row["first_name"] == "Анна"
        assert row["total_visits"] == 1
        assert row["is_returning"] is False
        assert row["at_risk"] is False
        # No phone, in any form. Owner decision DRF-1039 / OD-W2-2
        # (DRF-1360): the roster shipped "+7 ••• ••• 45 67" — four digits
        # of the customer's number — until that decision removed it. The
        # class-level guard lives in test_pii_boundary.py.
        assert "phone_masked" not in row
        assert not any("4567" in str(v) for v in row.values())

    def test_roster_sorted_by_last_visit_desc(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _make_client(tenant=tenant, channel_user_id="a", client_name="Анна")
        boris = _make_client(tenant=tenant, channel_user_id="b", client_name="Борис")
        clara = _make_client(tenant=tenant, channel_user_id="c", client_name="Клара")
        # Anna's last visit: 2026-05-20.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=anna,
            visit_local=datetime(2026, 5, 20, 14, 0),
        )
        # Boris's last visit: 2026-05-22 (most recent).
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=boris,
            visit_local=datetime(2026, 5, 22, 14, 0),
        )
        # Clara's last visit: 2026-05-15 (oldest).
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=clara,
            visit_local=datetime(2026, 5, 15, 14, 0),
        )
        out = cs.list_master_customers(master=accepted_master)
        names = [r["first_name"] for r in out]
        assert names == ["Борис", "Анна", "Клара"]

    def test_returning_customer_flag(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        anna = _make_client(tenant=tenant, channel_user_id="r1", client_name="Анна")
        for day in (10, 15, 20):
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=anna,
                visit_local=datetime(2026, 5, day, 14, 0),
            )
        out = cs.list_master_customers(master=accepted_master)
        assert len(out) == 1
        assert out[0]["total_visits"] == 3
        assert out[0]["is_returning"] is True

    def test_at_risk_flag_old_enough_returning_customer(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        # Pin now to evaluate at_risk deterministically.
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        anna = _make_client(tenant=tenant, channel_user_id="r2", client_name="Анна")
        # 3 historical visits, last 70 days ago — at_risk should fire.
        for offset in (90, 80, 70):
            visit_at = now - timedelta(days=offset)
            BookingRequest.all_tenants.create(
                tenant=tenant,
                master=accepted_master,
                bot_user=anna,
                service_name="маникюр",
                client_name="Анна",
                client_phone="+79000000000",
                visit_at=visit_at,
                duration_min=60,
                status=BookingRequest.Status.CONFIRMED,
            )
        out = cs.list_master_customers(master=accepted_master, now=now)
        assert len(out) == 1
        assert out[0]["at_risk"] is True
        assert out[0]["is_returning"] is True

    def test_at_risk_skipped_when_visits_below_threshold(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        # Only 1 visit a long time ago → not at_risk (cold lead, not lost regular).
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        anna = _make_client(tenant=tenant, channel_user_id="r3", client_name="Анна")
        BookingRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            bot_user=anna,
            service_name="маникюр",
            client_name="Анна",
            client_phone="+79000000000",
            visit_at=now - timedelta(days=90),
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
        )
        out = cs.list_master_customers(master=accepted_master, now=now)
        assert out[0]["at_risk"] is False

    def test_cancelled_bookings_excluded(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _make_client(tenant=tenant, channel_user_id="x1", client_name="Анна")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=anna,
            visit_local=datetime(2026, 5, 20, 14, 0),
            status=BookingRequest.Status.CANCELLED,
        )
        out = cs.list_master_customers(master=accepted_master)
        assert out == []

    def test_walkin_without_bot_user_skipped(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        # Snapshot-only legacy row (no BotUser link) must not pollute the roster.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=None,
            visit_local=datetime(2026, 5, 20, 14, 0),
        )
        out = cs.list_master_customers(master=accepted_master)
        assert out == []

    def test_last_visit_service_name_uses_most_recent_row(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _make_client(tenant=tenant, channel_user_id="svc", client_name="Анна")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=anna,
            visit_local=datetime(2026, 5, 10, 14, 0),
            service_name="старая услуга",
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=anna,
            visit_local=datetime(2026, 5, 20, 14, 0),
            service_name="новая услуга",
        )
        out = cs.list_master_customers(master=accepted_master)
        assert out[0]["last_visit_service_name"] == "новая услуга"


# --- cross-tenant isolation ---------------------------------------------


class TestCrossTenantIsolation:
    def test_sibling_tenant_customers_not_visible(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        other_tenant: Tenant,
    ) -> None:
        # Tenant A: 1 customer "Алиса".
        alisa = _make_client(tenant=tenant, channel_user_id="A", client_name="Алиса")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=alisa,
            visit_local=datetime(2026, 5, 20, 14, 0),
        )
        # Tenant B: 1 master + 1 customer "Боб" — must NOT leak.
        other_master = make_master(
            other_tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
        )
        bob = _make_client(tenant=other_tenant, channel_user_id="B", client_name="Боб")
        _make_booking(
            tenant=other_tenant,
            master=other_master,
            bot_user=bob,
            visit_local=datetime(2026, 5, 20, 14, 0),
        )
        out = cs.list_master_customers(master=accepted_master)
        assert len(out) == 1
        assert out[0]["first_name"] == "Алиса"
        # And the other_master sees only "Боб".
        out2 = cs.list_master_customers(master=other_master)
        assert len(out2) == 1
        assert out2[0]["first_name"] == "Боб"

    def test_endpoint_cross_tenant_isolation(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        other_tenant: Tenant,
    ) -> None:
        # Sibling tenant has customers + bookings; the calling master's GET
        # response must only contain the calling tenant's data.
        other_master = make_master(
            other_tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
        )
        leak = _make_client(tenant=other_tenant, channel_user_id="leak", client_name="Вторженец")
        _make_booking(
            tenant=other_tenant,
            master=other_master,
            bot_user=leak,
            visit_local=datetime(2026, 5, 20, 14, 0),
        )
        resp = client.get(
            reverse("master_api:customers_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json() == {"customers": []}
