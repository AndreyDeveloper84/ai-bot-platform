"""DRF-2537 — читатель отметки: экран здоровья различает «известно» и «неизвестно».

Секция «Зеркало броней» на экране здоровья контура считает незакрытые визиты
зеркала с отметкой последнего события канона и без неё и показывает возраст
старейшей отметки. Закрытые визиты не считаются. И экран обязан сказать вслух,
что возраст — не свежесть.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.test import Client

from apps.adminconsole.health import booking_mirror_marks, collect_report
from apps.booking.models import RemoteBookingProxy
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="marks-2537", name="Marks salon")


def _row(tenant: Tenant, status: str, event_at: dt.datetime | None, name: str = "") -> None:
    RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=None,
        start_at=NOW + dt.timedelta(days=1),
        end_at=NOW + dt.timedelta(days=1, hours=1),
        status=status,
        last_applied_event_name=name,
        last_applied_event_at=event_at,
    )


class TestKnownUnknownAndAge:
    def test_counts_and_oldest_age(self, tenant: Tenant) -> None:
        _row(tenant, "confirmed", NOW - dt.timedelta(hours=3), "booking.confirmed")
        _row(tenant, "pending_payment", NOW - dt.timedelta(days=2, hours=5), "booking.created")
        _row(tenant, "confirmed", None)  # до DRF-2537 или записано ботом
        _row(tenant, "completed", NOW - dt.timedelta(days=30), "booking.completed")  # закрыт

        marks = booking_mirror_marks(now=NOW)

        assert (marks.open_total, marks.known, marks.unknown) == (3, 2, 1)
        assert marks.oldest_known_age_seconds == dt.timedelta(days=2, hours=5).total_seconds()
        assert marks.oldest_known_event == "booking.created"
        assert marks.oldest_known_age_human == "2д05ч"

    def test_only_unknown_has_no_age(self, tenant: Tenant) -> None:
        _row(tenant, "confirmed", None)
        marks = booking_mirror_marks(now=NOW)
        # Наличие впереди: строка посчитана, и она «неизвестно», а не пропущена.
        assert (marks.open_total, marks.unknown) == (1, 1)
        assert marks.known == 0
        assert marks.oldest_known_age_seconds is None
        assert marks.oldest_known_age_human == "—"


class TestReportAndScreen:
    def test_report_carries_marks_without_raising_a_problem(self, tenant: Tenant) -> None:
        _row(tenant, "confirmed", NOW - dt.timedelta(days=40), "booking.confirmed")

        report = collect_report(now=NOW, counter=_NoUpstream())

        assert report.booking_marks is not None
        assert report.booking_marks.known == 1
        # Порога «слишком давно» нет: возраст не отставание, крика нет.
        assert not any("событи" in problem for problem in report.problems)

    def test_screen_names_the_limit(self, tenant: Tenant, admin_user) -> None:
        _row(tenant, "confirmed", NOW - dt.timedelta(hours=1), "booking.confirmed")
        client = Client()
        client.force_login(admin_user)

        html = client.get("/admin/health/").content.decode()

        assert "Зеркало броней: последнее событие канона" in html
        assert "Это не свежесть" in html


class _NoUpstream:
    """Бэкенд не опрашивается — секция зеркала броней от него не зависит."""

    def count(self, *, tenant_id: str, resource: str) -> int | None:
        return None
