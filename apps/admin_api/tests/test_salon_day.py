"""The salon's day — projection + endpoint (Phase 2).

The weight here is on the things that made the previous surfaces lie:
reading the source that actually holds the bookings, keeping «no visits»
distinguishable from «cannot see the visits», and never letting a phone
number reach the response.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.services.salon_day import build_salon_day, day_bounds_utc, tenant_tz
from apps.admin_api.tests.conftest import init_data_header, make_master
from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")


def _url(date: str | None = None) -> str:
    base = reverse("admin_api:salon_day")
    return f"{base}?date={date}" if date else base


def _client_bot_user(tenant: Tenant, *, name: str = "Мария Иванова", idx: int = 1) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"7{idx:03d}",
        client_name=name,
        display_name="max-handle",
        chat_id=f"7{idx:03d}",
        phone="+79991234567",
    )


def _service(tenant: Tenant, *, name: str = "Маникюр гель-лак") -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=4242,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug="manicure",
        name=name,
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.uuid4(),
    )


def _visit(
    tenant: Tenant,
    master,
    *,
    start_local: datetime,
    status: str = RemoteBookingProxy.Status.CONFIRMED,
    duration_min: int = 60,
    bot_user: BotUser | None = None,
    service: CatalogService | None = None,
    specialist_id=None,
) -> RemoteBookingProxy:
    start = start_local.astimezone(timezone.utc)
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=bot_user,
        start_at=start,
        end_at=start + timedelta(minutes=duration_min),
        status=status,
        specialist_id=specialist_id if specialist_id is not None else master.id,
        service_id=service.ayla_service_id if service else None,
    )


class TestProjection:
    def test_groups_visits_under_their_master(self, tenant: Tenant) -> None:
        anna = make_master(tenant, name="Анна Петрова", external_id=1)
        make_master(tenant, name="Сазонова Инна", external_id=2)
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        _visit(tenant, anna, start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK))
        _visit(tenant, anna, start_local=datetime(2026, 8, 20, 12, 0, tzinfo=MSK))

        result = build_salon_day(tenant, day=day)

        by_name = {m.name: m for m in result.masters}
        assert len(by_name["Анна Петрова"].visits) == 2
        # A master with nothing booked is an empty column, not a missing one:
        # «Инна сегодня свободна» is an answer.
        assert by_name["Сазонова Инна"].visits == []
        assert result.summary.total == 2

    def test_day_boundary_is_tenant_local_not_utc(self, tenant: Tenant) -> None:
        """01:00 MSK belongs to today, even though it is 22:00 UTC yesterday."""
        master = make_master(tenant, name="Анна", external_id=1)
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        _visit(tenant, master, start_local=datetime(2026, 8, 20, 1, 0, tzinfo=MSK))

        result = build_salon_day(tenant, day=day)
        assert result.summary.total == 1

        # ...and the previous local day does not claim it.
        prev = build_salon_day(tenant, day=day - timedelta(days=1))
        assert prev.summary.total == 0

    def test_cancelled_visits_are_shown_not_filtered(self, tenant: Tenant) -> None:
        """A freed slot and an absent booking must not look the same."""
        master = make_master(tenant, name="Анна", external_id=1)
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        _visit(
            tenant,
            master,
            start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK),
            status=RemoteBookingProxy.Status.CANCELLED,
        )

        result = build_salon_day(tenant, day=day)
        assert result.summary.total == 1
        assert result.summary.released == 1
        assert result.summary.upcoming == 0
        assert result.masters[0].visits[0].is_released is True

    def test_visit_for_unknown_specialist_becomes_an_orphan_not_a_silence(
        self, tenant: Tenant
    ) -> None:
        """A booking nobody can see is the failure this window exists to stop."""
        master = make_master(tenant, name="Анна", external_id=1)
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        _visit(
            tenant,
            master,
            start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK),
            specialist_id=uuid.uuid4(),
        )

        result = build_salon_day(tenant, day=day)
        assert len(result.orphan_visits) == 1
        assert result.summary.total == 1

    def test_in_progress_is_computed_against_now(self, tenant: Tenant) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        _visit(tenant, master, start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK))

        mid = datetime(2026, 8, 20, 10, 30, tzinfo=MSK)
        result = build_salon_day(tenant, day=day, now=mid)
        assert result.masters[0].visits[0].is_in_progress is True

        later = datetime(2026, 8, 20, 13, 0, tzinfo=MSK)
        result = build_salon_day(tenant, day=day, now=later)
        assert result.masters[0].visits[0].is_in_progress is False

    def test_cancelled_visit_is_never_in_progress(self, tenant: Tenant) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        _visit(
            tenant,
            master,
            start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK),
            status=RemoteBookingProxy.Status.CANCELLED,
        )
        mid = datetime(2026, 8, 20, 10, 30, tzinfo=MSK)
        result = build_salon_day(tenant, day=day, now=mid)
        assert result.masters[0].visits[0].is_in_progress is False

    def test_names_resolve_from_bot_user_and_catalog(self, tenant: Tenant) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        bu = _client_bot_user(tenant)
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        _visit(
            tenant,
            master,
            start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK),
            bot_user=bu,
            service=service,
        )

        visit = build_salon_day(tenant, day=day).masters[0].visits[0]
        assert visit.client_first_name == "Мария"
        assert visit.client_last_initial == "И."
        assert visit.service_name == "Маникюр гель-лак"

    def test_orphan_booking_without_bot_user_is_a_guest(self, tenant: Tenant) -> None:
        """Three of the pilot's 23 mirror rows have no BotUser at all."""
        master = make_master(tenant, name="Анна", external_id=1)
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        _visit(tenant, master, start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK))

        visit = build_salon_day(tenant, day=day).masters[0].visits[0]
        assert visit.client_first_name == "Гость"

    def test_archived_master_is_not_a_column(self, tenant: Tenant) -> None:
        make_master(
            tenant,
            name="Ушедшая",
            external_id=3,
            archived_at=datetime.now(tz=timezone.utc),
        )
        day = datetime(2026, 8, 20, tzinfo=MSK).date()
        result = build_salon_day(tenant, day=day)
        assert [m.name for m in result.masters] == []


class TestTimezoneHelpers:
    def test_bad_timezone_falls_back_instead_of_raising(self, tenant: Tenant) -> None:
        tenant.timezone = "Not/AZone"
        assert str(tenant_tz(tenant)) == "Europe/Moscow"

    def test_day_bounds_span_exactly_24h(self) -> None:
        start, end = day_bounds_utc(datetime(2026, 8, 20, tzinfo=MSK).date(), MSK)
        assert (end - start) == timedelta(days=1)


class TestEndpoint:
    def test_owner_gets_the_day(self, client: Client, owner_bot_user, tenant: Tenant) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        _visit(tenant, master, start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK))

        resp = client.get(_url("2026-08-20"), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == "2026-08-20"
        assert data["timezone"] == "Europe/Moscow"
        assert data["summary"]["total"] == 1
        assert data["masters"][0]["name"] == "Анна"
        assert data["orphan_visits"] == []

    def test_admin_may_read_it_too(self, client: Client, admin_bot_user, tenant: Tenant) -> None:
        resp = client.get(_url("2026-08-20"), HTTP_AUTHORIZATION=init_data_header("5002"))
        assert resp.status_code == 200

    def test_master_only_is_forbidden(
        self, client: Client, master_only_bot_user, tenant: Tenant
    ) -> None:
        resp = client.get(_url("2026-08-20"), HTTP_AUTHORIZATION=init_data_header("5004"))
        assert resp.status_code == 403

    def test_unauthenticated_is_rejected(self, client: Client, tenant: Tenant) -> None:
        resp = client.get(_url("2026-08-20"))
        assert resp.status_code == 400

    def test_bad_date_is_a_400_not_a_500(
        self, client: Client, owner_bot_user, tenant: Tenant
    ) -> None:
        resp = client.get(_url("20-08-2026"), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_date_defaults_to_tenant_today(
        self, client: Client, owner_bot_user, tenant: Tenant
    ) -> None:
        resp = client.get(_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        expected = datetime.now(tz=timezone.utc).astimezone(tenant_tz(tenant)).date()
        assert resp.json()["date"] == expected.isoformat()

    def test_response_carries_no_phone_anywhere(
        self, client: Client, owner_bot_user, tenant: Tenant
    ) -> None:
        """DRF-1039 — asserted on the serialised body, not on intent."""
        master = make_master(tenant, name="Анна", external_id=1)
        bu = _client_bot_user(tenant)
        _visit(
            tenant,
            master,
            start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK),
            bot_user=bu,
        )

        resp = client.get(_url("2026-08-20"), HTTP_AUTHORIZATION=init_data_header("5001"))
        body = resp.content.decode()
        assert "+79991234567" not in body
        assert "79991234567" not in body
        assert "phone" not in body

    def test_surname_is_reduced_to_an_initial(
        self, client: Client, owner_bot_user, tenant: Tenant
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        bu = _client_bot_user(tenant, name="Мария Иванова")
        _visit(
            tenant,
            master,
            start_local=datetime(2026, 8, 20, 10, 0, tzinfo=MSK),
            bot_user=bu,
        )

        resp = client.get(_url("2026-08-20"), HTTP_AUTHORIZATION=init_data_header("5001"))
        body = resp.content.decode()
        assert "Иванова" not in body
        visit = resp.json()["masters"][0]["visits"][0]
        assert visit["client_first_name"] == "Мария"
        assert visit["client_last_initial"] == "И."
