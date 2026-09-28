"""DRF-2589 — время визита на проводе Mini App в поясе САЛОНА записи.

Замер стенда (главное окно, 28.09): у ``mkt-lumina`` визит в 09:00 по салону
уходил как ``06:00+00:00``. Экраны берут часы из строки (``formatVisitFull``),
и человек видел «в 06:00».

Узлы «поле непустое» и «момент верный» прошли бы и при дефекте: момент-то
верный, врёт представление. Поэтому пара, которая обязана различаться, —
один момент UTC у салонов в разных поясах даёт разные часы В СТРОКЕ.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.miniapp_api.tests.test_person_owns_booking_2436 import (  # noqa: F401 — autouse
    AYLA_UID,
    ME,
    _get,
    _identity,
    _settings,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/customer/bookings/list"
HOME_URL = "/api/v1/customer/recent-activity"


@pytest.fixture
def home() -> Tenant:
    # Салон, под которым Mini App узнаёт человека (MAX_BOT_TENANT_SLUG).
    return Tenant.objects.create(slug="home-2436", name="Формула тела", timezone="Europe/Moscow")


@pytest.fixture
def ekb() -> Tenant:
    return Tenant.objects.create(slug="ekb-2589", name="Люмина", timezone="Asia/Yekaterinburg")


def _visit_at_utc(days: int, hour_utc: int = 6) -> datetime:
    day = (timezone.now() + timedelta(days=days)).astimezone(ZoneInfo("UTC"))
    return day.replace(hour=hour_utc, minute=0, second=0, microsecond=0)


def _proxy(tenant: Tenant, bot_user, start: datetime) -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=uuid.uuid4(),
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="confirmed",
    )


def _hhmm(iso: str) -> str:
    return iso[11:16]


def test_list_carries_the_salons_hour_in_the_string_two_zones_two_hours(client, home, ekb) -> None:
    start = _visit_at_utc(3)  # 06:00 UTC
    msk = _proxy(home, _identity(home, ME, AYLA_UID), start)
    yek = _proxy(ekb, _identity(ekb, ME, AYLA_UID), start + timedelta(days=1))

    items = {i["id"]: i["visit_at"] for i in _get(client, LIST_URL).json()["items"]}

    assert _hhmm(items[str(msk.appointment_id)]) == "09:00"
    assert _hhmm(items[str(yek.appointment_id)]) == "11:00"
    # Тот же момент — фронт через new Date() читает его прежним.
    assert datetime.fromisoformat(items[str(msk.appointment_id)]) == start


def test_detail_carries_the_salons_hour(client, home) -> None:
    start = _visit_at_utc(3)
    msk = _proxy(home, _identity(home, ME, AYLA_UID), start)

    visit_at = _get(client, f"/api/v1/customer/bookings/{msk.appointment_id}").json()["booking"][
        "visit_at"
    ]

    assert _hhmm(visit_at) == "09:00"
    assert not visit_at.endswith("+00:00")


def test_home_next_booking_is_worded_in_the_bookings_salon_zone(client, home, ekb) -> None:
    """Ближайшая запись — в Екатеринбурге, а Mini App узнал человека под
    московским салоном: час — салона ЗАПИСИ (11:00), не запроса (09:00)."""
    _identity(home, ME, AYLA_UID)
    _proxy(ekb, _identity(ekb, ME, AYLA_UID), _visit_at_utc(3))

    nb = _get(client, HOME_URL).json()["next_booking"]

    assert "11:00" in nb["date_human"]
    assert "09:00" not in nb["date_human"]
