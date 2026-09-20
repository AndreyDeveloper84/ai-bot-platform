"""Конфликт графика с записью показывает саму запись (DRF-2200, экран 4).

Макет DRF-1186 экран 4 — «На это время уже есть запись» с карточкой
клиента, а не со словом «конфликт». Каталог на записи часов отвечает 409
``has_active_appointments`` и НЕ говорит какими записями, поэтому состав
собирается здесь — тем же вычислителем, что рисует «Расписание»
(:func:`build_schedule`). Второй вычислитель разошёлся бы с экраном, и
мастер увидел бы конфликт, которого в его расписании нет.

* c1 — запись вне предложенной смены попадает в ``details.conflicts``
  с полями карточки (кто, что, когда);
* c2 — запись ВНУТРИ предложенной смены не мешает: 409 остаётся (его
  сказал каталог), но список пуст;
* c3 — узел GO: состав конфликтов совпадает с тем, что «Расписание»
  показывает на тот же день;
* c4 — прошедшее время не конфликтует: менять график назад нечего;
* c5 — граница PII (DRF-1039/1360): клиент назван именем и инициалом, как
  на «Расписании», телефона в отказе нет;
* c6 — у даты со своим исключением рамку задаёт исключение, а не шаблон:
  такой день не конфликтует с правкой шаблона (её он всё равно не слышит);
* c7 — запись через полночь не «помещается» по часам: сравниваются моменты;
* c8 — перерыв, заведённый поверх записи, — такой же конфликт.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import ScheduleBlockConflictError
from apps.master_api import views
from apps.master_api.pii import find_forbidden_pii
from apps.master_api.services.schedule import build_schedule
from apps.master_api.tests.conftest import init_data_header
from apps.scheduling.models import ScheduleException, WorkingHours
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
#: Понедельник, 12:00 MSK — день недели и «сейчас» заперты, иначе тест
#: поедет вместе с календарём.
FROZEN = "2026-09-21 09:00:00"
WEDNESDAY = 2
WEDNESDAY_DATE = "2026-09-23"
CUSTOMER_PHONE = "+79997775544"


def _week(
    *,
    wednesday_working: bool,
    start: str = "10:00",
    end: str = "19:00",
    break_start: str | None = None,
    break_end: str | None = None,
) -> list[dict]:
    """Предложенное тело PUT: семь дней, среда — по параметру."""

    return [
        {
            "day_of_week": d,
            "is_working_day": True if d != WEDNESDAY else wednesday_working,
            "start_time": start if (d != WEDNESDAY or wednesday_working) else None,
            "end_time": end if (d != WEDNESDAY or wednesday_working) else None,
            "break_start": break_start,
            "break_end": break_end,
        }
        for d in range(7)
    ]


def _booking(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    local: datetime,
    duration_min: int = 60,
    service_name: str = "Классический массаж",
    client_name: str = "Анна Петрова",
    status: str = "confirmed",
) -> RemoteBookingProxy:
    service_id = uuid4()
    CatalogService.all_tenants.create(
        tenant=tenant,
        ayla_service_id=service_id,
        external_id=None,
        external_updated_at=datetime.now(dt_timezone.utc),
        name=service_name,
        duration_min=duration_min,
    )
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"cust-{uuid4().hex[:12]}",
        client_name=client_name,
        phone=CUSTOMER_PHONE,
    )
    start_utc = local.replace(tzinfo=MSK).astimezone(dt_timezone.utc)
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid4(),
        tenant=tenant,
        bot_user=bot_user,
        specialist_id=master.id,
        service_id=service_id,
        start_at=start_utc,
        end_at=start_utc + timedelta(minutes=duration_min),
        status=status,
    )


@pytest.fixture
def ayla_refuses(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    """Каталог говорит «есть записи» — состав собирает бот."""

    client = MagicMock()
    client.put_working_hours.side_effect = ScheduleBlockConflictError("has_active_appointments")
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


@pytest.fixture
def workweek(tenant: Tenant, accepted_master: CatalogMaster) -> None:
    """Текущий шаблон: работает всю неделю 10:00–19:00."""

    for weekday in range(7):
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            day_of_week=weekday,
            is_working=True,
            start_time="10:00",
            end_time="19:00",
        )


def _put(client: Client, schedule: list[dict]):
    return client.put(
        reverse("master_api:working_hours"),
        data={"schedule": schedule},
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


@freeze_time(FROZEN)
class TestConflictsCarryTheBooking:
    def test_booking_outside_the_proposed_shift_is_named(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        _booking(tenant=tenant, master=accepted_master, local=datetime(2026, 9, 23, 14, 30))
        resp = _put(client, _week(wednesday_working=False))
        assert resp.status_code == 409, resp.content
        body = resp.json()
        assert body["error"] == "has_active_appointments"
        conflicts = body["details"]["conflicts"]
        assert len(conflicts) == 1
        row = conflicts[0]
        assert row["date"] == WEDNESDAY_DATE
        assert row["client_name"] == "Анна П."
        assert row["service_name"] == "Классический массаж"
        assert row["duration_min"] == 60
        assert row["start_at"].startswith("2026-09-23T14:30")
        assert row["end_at"].startswith("2026-09-23T15:30")

    def test_booking_inside_the_proposed_shift_does_not_conflict(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        _booking(tenant=tenant, master=accepted_master, local=datetime(2026, 9, 23, 14, 30))
        # Присутствие: та же запись при «не работаю» в конфликте (см. c1) —
        # значит пустота ниже про смену, а не про пустой список вообще.
        assert _put(client, _week(wednesday_working=False)).json()["details"]["conflicts"]
        resp = _put(client, _week(wednesday_working=True, start="10:00", end="19:00"))
        assert resp.status_code == 409, resp.content
        assert resp.json()["details"]["conflicts"] == []

    def test_composition_matches_what_schedule_shows_for_that_day(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        for hour in (11, 14, 17):
            _booking(
                tenant=tenant,
                master=accepted_master,
                local=datetime(2026, 9, 23, hour, 0),
                client_name=f"Клиент{hour} Тестов",
            )
        conflicts = _put(client, _week(wednesday_working=False)).json()["details"]["conflicts"]
        payload = build_schedule(
            accepted_master,
            from_date=datetime(2026, 9, 23).date(),
            to_date=datetime(2026, 9, 23).date(),
        )
        shown = [b.booking_id for day in payload.days for b in day.bookings]
        assert shown, "«Расписание» на этот день должно показывать записи"
        assert [row["booking_id"] for row in conflicts] == shown

    def test_past_time_is_not_a_conflict(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        # Сегодня 12:00 MSK: запись в 10:30 уже прошла, в 17:00 — впереди.
        _booking(tenant=tenant, master=accepted_master, local=datetime(2026, 9, 21, 10, 30))
        later = _booking(tenant=tenant, master=accepted_master, local=datetime(2026, 9, 21, 17, 0))
        monday = [
            (
                dict(row, is_working_day=False, start_time=None, end_time=None)
                if row["day_of_week"] == 0
                else row
            )
            for row in _week(wednesday_working=True)
        ]
        conflicts = _put(client, monday).json()["details"]["conflicts"]
        assert [row["booking_id"] for row in conflicts] == [str(later.appointment_id)]


class TestConflictsKeepThePiiBoundary:
    """Отказ уходит на мастерскую поверхность — телефону клиента там не место."""

    @freeze_time(FROZEN)
    def test_conflict_names_the_client_without_a_phone(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        booking = _booking(
            tenant=tenant, master=accepted_master, local=datetime(2026, 9, 23, 14, 30)
        )
        resp = _put(client, _week(wednesday_working=False))
        body = resp.json()
        # Присутствие: клиент в отказе назван — значит отсутствие ниже про телефон.
        assert body["details"]["conflicts"][0]["client_name"] == "Анна П."
        assert find_forbidden_pii(body) == []  # empty-assert-ok: тело проверено строкой выше
        raw = resp.content.decode()
        # Присутствие в той же строке (кириллица в JSON экранирована, поэтому
        # сверяемся с id записи): запись в теле есть — значит ниже проверяется
        # телефон, а не пустой ответ.
        assert str(booking.appointment_id) in raw
        assert CUSTOMER_PHONE not in raw
        assert "9997775544" not in raw
        assert "5544" not in raw


@freeze_time(FROZEN)
class TestWhatTheTemplateCannotReach:
    """Правка шаблона не слышна там, где рамку задаёт не шаблон."""

    def test_a_date_with_its_own_exception_is_not_a_conflict(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        # Присутствие: без исключения эта же запись — конфликт.
        _booking(tenant=tenant, master=accepted_master, local=datetime(2026, 9, 23, 14, 30))
        assert _put(client, _week(wednesday_working=False)).json()["details"]["conflicts"]

        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            date=datetime(2026, 9, 23).date(),
            type=ScheduleException.Type.CUSTOM_HOURS,
            start_time="08:00",
            end_time="16:00",
        )
        conflicts = _put(client, _week(wednesday_working=False)).json()["details"]["conflicts"]
        assert conflicts == []

    def test_a_booking_across_midnight_still_conflicts(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        # 23:30 + 60 мин кончается 00:30 — по ЧАСАМ это «раньше 19:00».
        _booking(tenant=tenant, master=accepted_master, local=datetime(2026, 9, 23, 23, 30))
        conflicts = _put(client, _week(wednesday_working=True)).json()["details"]["conflicts"]
        assert [row["start_at"][11:16] for row in conflicts] == ["23:30"]

    def test_a_break_laid_over_a_booking_is_a_conflict(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        _booking(tenant=tenant, master=accepted_master, local=datetime(2026, 9, 23, 13, 0))
        # Присутствие: без перерыва запись помещается в смену.
        assert _put(client, _week(wednesday_working=True)).json()["details"]["conflicts"] == []
        conflicts = _put(
            client,
            _week(wednesday_working=True, break_start="13:00", break_end="14:00"),
        ).json()["details"]["conflicts"]
        assert [row["start_at"][11:16] for row in conflicts] == ["13:00"]

    def test_the_horizon_is_named_in_the_body(
        self, client: Client, tenant, bot_user, accepted_master, workweek, ayla_refuses
    ):
        body = _put(client, _week(wednesday_working=False)).json()
        assert body["details"]["horizon_days"] == 14
