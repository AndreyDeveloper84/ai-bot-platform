"""Week summary on the master dashboard (DRF-1846, cabinet map K2 — D07, D09).

What is locked:

* the calendar week is Monday 00:00 – Sunday 23:59 in the TENANT's zone:
  a visit at 00:30 on Monday and one at 23:30 on Sunday (MSK) are in, the
  neighbours a minute across either edge are out — both are a different
  UTC day, so a UTC week would get them wrong;
* cancelled / no-show visits did not take the master's time and are not
  counted; another master's visits are not counted;
* the rating is shown only with at least one review behind it; a seeded
  rating with ``review_count == 0`` is «no data»;
* the block carries exactly five keys — there is no revenue, because the
  booking mirror has no price.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.master_api.services import dashboard as ds
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.master_api.tests.test_dashboard import MSK, _make_booking, _utc
from apps.tenancy.models import Tenant

# Thursday 2026-05-21, 14:00 MSK → the week is 18–24 May.
NOW = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))


def _visit(tenant, master, local: datetime, status: str = "confirmed") -> None:
    _make_booking(tenant=tenant, master=master, visit_local=local, status=status)


class TestCounts:
    def test_the_week_is_the_tenants_calendar_week(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        m = accepted_master
        _visit(tenant, m, datetime(2026, 5, 18, 0, 30, tzinfo=MSK), "completed")  # Mon, in
        _visit(tenant, m, datetime(2026, 5, 20, 12, 0, tzinfo=MSK), "completed")  # Wed, in
        _visit(tenant, m, datetime(2026, 5, 24, 23, 30, tzinfo=MSK))  # Sun, in
        _visit(tenant, m, datetime(2026, 5, 17, 23, 30, tzinfo=MSK), "completed")  # prev Sun
        _visit(tenant, m, datetime(2026, 5, 25, 0, 30, tzinfo=MSK))  # next Mon

        week = ds.get_week_summary(m, NOW)

        assert (week.week_start, week.week_end) == ("2026-05-18", "2026-05-24")
        assert week.bookings == 3
        assert week.completed == 2

    def test_released_visits_and_other_masters_are_not_counted(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        m = accepted_master
        _visit(tenant, m, datetime(2026, 5, 19, 10, 0, tzinfo=MSK))
        _visit(tenant, m, datetime(2026, 5, 19, 12, 0, tzinfo=MSK), "cancelled")
        _visit(tenant, m, datetime(2026, 5, 19, 14, 0, tzinfo=MSK), "no_show")
        peer = make_master(tenant, name="Коллега", invite_status=CatalogMaster.InviteStatus.ACCEPTED)
        _visit(tenant, peer, datetime(2026, 5, 19, 16, 0, tzinfo=MSK), "completed")

        week = ds.get_week_summary(m, NOW)

        assert week.bookings == 1
        assert week.completed == 0

    def test_an_empty_week_is_zero_not_absent(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        week = ds.get_week_summary(accepted_master, NOW)
        assert (week.bookings, week.completed) == (0, 0)


class TestRating:
    def _rate(self, master: CatalogMaster, rating: str | None, count: int) -> None:
        master.rating = Decimal(rating) if rating is not None else None
        master.review_count = count
        master.save(update_fields=["rating", "review_count"])

    def test_backed_by_reviews_is_shown(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        self._rate(accepted_master, "4.80", 12)
        assert ds.get_week_summary(accepted_master, NOW).rating == {
            "value": 4.8,
            "review_count": 12,
        }

    def test_a_seeded_rating_without_reviews_is_no_data(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        self._rate(accepted_master, "4.80", 0)
        assert ds.get_week_summary(accepted_master, NOW).rating is None

    def test_zero_is_no_data(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        self._rate(accepted_master, "0.00", 3)
        assert ds.get_week_summary(accepted_master, NOW).rating is None

    def test_none_is_no_data(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        self._rate(accepted_master, None, 3)
        assert ds.get_week_summary(accepted_master, NOW).rating is None


class TestShape:
    def test_exactly_five_keys_and_no_revenue(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        out = ds.build_dashboard(accepted_master, NOW).to_dict()
        assert set(out["week_summary"]) == {
            "week_start",
            "week_end",
            "bookings",
            "completed",
            "rating",
        }

    def test_the_endpoint_serves_it(
        self, client: Client, bot_user: BotUser, accepted_master: CatalogMaster
    ) -> None:
        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        week = resp.json()["week_summary"]
        assert isinstance(week["bookings"], int)
        assert week["rating"] is None
