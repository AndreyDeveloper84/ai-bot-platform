"""Окно дашборда мастера считается по каталогу, а не по локальной копии (DRF-2014).

Замер главного окна (хост `ruvds-o1mqo`, база `ayla-bot-staging-postgres-1`,
15.09.2026 ~23:20 UTC, `BEGIN READ ONLY`; у наблюдения есть срок годности):
в боте `scheduling_workinghours` — 28 строк у 4 мастеров, последнее изменение
22.07.2026, в каталоге — 63 строки у 9 мастеров; `scheduling_scheduleexception`
— 0 строк. Занятость дашборд при этом берёт из каталожного зеркала
`RemoteBookingProxy` (`services/visit_source.py:181`).

Отсюда дефект: «ближайшее свободное окно» — пересечение **устаревшей рамки**
со **свежей занятостью**. Это не просто старые данные: окно показывается там,
где в каталоге мастер не работает, и прячется там, где работает, но в копии
бота его нет.

Рамку берёт `_working_block_today` (`services/dashboard.py:582`) прямо из
локальных `ScheduleException` (`:590`) и `WorkingHours` (`:603`); файл
`dashboard.py` ни разу не спрашивает `BOOKING_VIA_AYLA_REST`. Экран расписания
и готовность мастера ходят через `services/schedule_frame.py:152 load_day_frame`,
который флаг спрашивает.

Здесь закрепляется:

* флаг включён — рамка каталожная, включая исключение на сегодня;
* флаг выключен — прежняя локальная рамка, и в каталог не ходим вовсе;
* каталог не читается — окна нет, и **молчаливого отката** к устаревшей копии
  тоже нет («не знаю» не равно «весь день свободен»).

Копию `apps/scheduling` этот лист не трогает: снятие копии — вопрос владельца
X5. Клиентский путь `miniapp_api/views.py:641` — вопрос X6. Третий безфлаговый
читатель кабинета (`services/schedule.py:913-919`) — отдельный лист DRF-2019.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from zoneinfo import ZoneInfo

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.salon_client import SalonNotConfigured
from apps.master_api.services import dashboard as ds
from apps.master_api.services import schedule_frame
from apps.master_api.tests.conftest import init_data_header
from apps.scheduling.models import WorkingHours
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
#: 21.05.2026 — четверг (`day_of_week=3`), тот же день, что у соседних тестов.
THURSDAY = dt.date(2026, 5, 21)
THURSDAY_WEEKDAY = 3
#: Имя причины в логе: наружу у мастера одно имя («окна нет»), внутрь —
#: раздельные причины. Третье состояние в контракте Mini App — вопрос X7,
#: внутри этого листа не вводится.
FRAME_UNREADABLE_LOG = "master.dashboard.frame_unreadable"


def _at(hour: int, minute: int = 0) -> dt.datetime:
    """Момент этого четверга в часовом поясе салона — «сейчас» для дашборда."""
    return dt.datetime(2026, 5, 21, hour, minute, tzinfo=MSK)


def _owner_staff(tenant: Tenant) -> None:
    """Активный владелец — от его имени рамка читается в каталоге."""
    from apps.identity.models import BotUser

    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"owner-{uuid.uuid4().hex[:8]}",
        ayla_user_id=str(uuid.uuid4()),
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER)


def _template(*, working: bool, start: str = "10:00", end: str = "19:00") -> list[dict]:
    """Недельный шаблон каталога: четверг работает или нет, остальные дни как есть."""
    return [
        {
            "day_of_week": i,
            "day_name": "",
            "is_working_day": working if i == THURSDAY_WEEKDAY else True,
            "start_time": start,
            "end_time": end,
            "break_start": None,
            "break_end": None,
        }
        for i in range(7)
    ]


def _exception(*, is_working_day: bool, start: str | None, end: str | None) -> list[dict]:
    return [
        {
            "id": "e-2014",
            "date": THURSDAY.isoformat(),
            "is_working_day": is_working_day,
            "start_time": start,
            "end_time": end,
            "break_start": None,
            "break_end": None,
            "note": "",
        }
    ]


class _FakeSalonClient:
    """Отвечает на три чтения рамки записанными строками."""

    def __init__(self, *, template=None, exceptions=None, time_off=None, exc=None) -> None:
        self._template = template or []
        self._exceptions = exceptions or []
        self._time_off = time_off or []
        self._exc = exc
        self.calls: list[str] = []

    def get_master_schedule(self, **kwargs):
        self.calls.append("schedule")
        if self._exc:
            raise self._exc
        return self._template

    def list_schedule_exceptions(self, **kwargs):
        self.calls.append("exceptions")
        return self._exceptions

    def list_time_off(self, **kwargs):
        self.calls.append("time_off")
        return self._time_off


class _ForbiddenSalonClient:
    """Каталог при выключенном флаге не спрашивают вовсе."""

    def __getattr__(self, name: str):
        def _boom(**kwargs):
            raise AssertionError(f"каталог не должен опрашиваться при флаге OFF: {name}")

        return _boom


@pytest.fixture
def catalog_master(tenant: Tenant, accepted_master: CatalogMaster) -> CatalogMaster:
    """Мастер как после синка: id строки равен id профиля в каталоге (DRF-1933)."""
    CatalogMaster.all_tenants.filter(pk=accepted_master.pk).update(
        catalog_specialist_id=accepted_master.pk
    )
    accepted_master.refresh_from_db()
    _owner_staff(tenant)
    return accepted_master


def _local_hours(
    tenant: Tenant, master: CatalogMaster, *, start=dt.time(10, 0), end=dt.time(20, 0)
):
    """Устаревшая локальная копия: четверг рабочий 10–20."""
    return WorkingHours.all_tenants.create(
        tenant=tenant,
        master=master,
        day_of_week=THURSDAY_WEEKDAY,
        is_working=True,
        start_time=start,
        end_time=end,
    )


def _use_client(monkeypatch, client) -> None:
    """Подмена там, где имя ищется — в модуле рамки (иначе меряем не тот путь)."""
    monkeypatch.setattr(schedule_frame, "get_salon_client", lambda: client)


# ── смешение источников: рамка устаревшая, занятость свежая ────────────────


def test_window_is_not_shown_when_the_catalog_says_the_master_does_not_work_today(
    tenant: Tenant, catalog_master: CatalogMaster, monkeypatch
) -> None:
    """«Показано несуществующее»: копия говорит «работает», каталог — «нет»."""
    _local_hours(tenant, catalog_master)
    fake = _FakeSalonClient(template=_template(working=False))
    _use_client(monkeypatch, fake)

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        summary = ds.get_today_summary(catalog_master, _at(10))

    assert summary.next_free_window is None
    # Положительный близнец к узлу «при флаге OFF каталог не зовут»: без него
    # «не зовут никогда» и «не зовут пока» неразличимы, и оборванная проводка
    # дала бы зелёными оба узла.
    assert "schedule" in fake.calls


def test_window_appears_when_only_the_catalog_has_the_hours(
    tenant: Tenant, catalog_master: CatalogMaster, monkeypatch
) -> None:
    """«Спрятано существующее»: в копии мастера нет, в каталоге он работает."""
    assert not WorkingHours.all_tenants.filter(master=catalog_master).exists()
    _use_client(monkeypatch, _FakeSalonClient(template=_template(working=True)))

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        summary = ds.get_today_summary(catalog_master, _at(10))

    assert summary.next_free_window == {"start": "10:00", "end": "19:00"}


def test_todays_catalog_exception_bounds_the_window(
    tenant: Tenant, catalog_master: CatalogMaster, monkeypatch
) -> None:
    """Разовое исключение каталога на сегодня сужает окно, а копия — нет."""
    _local_hours(tenant, catalog_master)
    _use_client(
        monkeypatch,
        _FakeSalonClient(
            template=_template(working=True),
            exceptions=_exception(is_working_day=True, start="12:00", end="16:00"),
        ),
    )

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        summary = ds.get_today_summary(catalog_master, _at(12))

    assert summary.next_free_window == {"start": "12:00", "end": "16:00"}


def test_catalog_day_off_exception_hides_the_window(
    tenant: Tenant, catalog_master: CatalogMaster, monkeypatch
) -> None:
    """Выходной по исключению каталога — окна нет, хотя копия говорит «работает»."""
    _local_hours(tenant, catalog_master)
    _use_client(
        monkeypatch,
        _FakeSalonClient(
            template=_template(working=True),
            exceptions=_exception(is_working_day=False, start=None, end=None),
        ),
    )

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        summary = ds.get_today_summary(catalog_master, _at(10))

    assert summary.next_free_window is None


# ── переключатель работает в обе стороны ───────────────────────────────────


def test_local_source_unchanged_when_the_flag_is_off(
    tenant: Tenant, catalog_master: CatalogMaster, monkeypatch
) -> None:
    """Положительная стража: при выключенном флаге — прежняя копия и ни одного
    обращения в каталог. Иначе «правило» было бы односторонней переделкой."""
    _local_hours(tenant, catalog_master)
    _use_client(monkeypatch, _ForbiddenSalonClient())

    with override_settings(BOOKING_VIA_AYLA_REST=False):
        summary = ds.get_today_summary(catalog_master, _at(10))

    assert summary.next_free_window == {"start": "10:00", "end": "20:00"}


def test_unreadable_catalog_gives_no_window_and_no_local_fallback(
    tenant: Tenant, catalog_master: CatalogMaster, monkeypatch, caplog
) -> None:
    """Каталог не прочитан — «не знаю», а не «весь день свободен» и не тихий
    откат к устаревшей копии (DRF-1111: канон не читается — отказ, не догадка).

    Наружу у мастера одно имя («окна нет») — третьего состояния в контракте
    Mini App нет, и заводить его здесь нельзя: это вопрос владельца **X7**.
    Значит различие обязано жить внутри, отдельной причиной в логе, иначе
    «расписание не прочиталось» и «сегодня выходной» неразличимы ни для кого.
    """
    _local_hours(tenant, catalog_master)
    _use_client(monkeypatch, _FakeSalonClient(exc=SalonNotConfigured("no owner staff")))

    with override_settings(BOOKING_VIA_AYLA_REST=True), caplog.at_level(logging.INFO):
        summary = ds.get_today_summary(catalog_master, _at(10))

    assert summary.next_free_window is None
    unreadable = [r.getMessage() for r in caplog.records if FRAME_UNREADABLE_LOG in r.getMessage()]
    assert unreadable, "причина не названа в логе"
    assert any("reason=SalonNotConfigured" in m for m in unreadable), unreadable


def test_a_day_off_is_not_logged_as_an_unreadable_frame(
    tenant: Tenant, catalog_master: CatalogMaster, monkeypatch, caplog
) -> None:
    """Вторая половина различия: «сегодня не работает» — не «не прочитали».

    Без этого узла причина в логе стояла бы на каждом пустом окне и не
    отличала бы ничего.
    """
    _local_hours(tenant, catalog_master)
    _use_client(monkeypatch, _FakeSalonClient(template=_template(working=False)))

    with override_settings(BOOKING_VIA_AYLA_REST=True), caplog.at_level(logging.INFO):
        summary = ds.get_today_summary(catalog_master, _at(10))

    assert summary.next_free_window is None
    assert not [r for r in caplog.records if FRAME_UNREADABLE_LOG in r.getMessage()]


# ── тот же ответ на самой ручке, не только в помощнике ─────────────────────


def test_dashboard_endpoint_window_follows_the_catalog(
    client: Client, tenant: Tenant, catalog_master: CatalogMaster, bot_user, monkeypatch
) -> None:
    """`GET /dashboard` целиком: копия говорит «работает», каталог — «нет»."""
    CatalogMaster.all_tenants.filter(pk=catalog_master.pk).update(linked_bot_user=bot_user)
    _local_hours(tenant, catalog_master)
    _use_client(monkeypatch, _FakeSalonClient(template=_template(working=False)))
    # Часы — молчаливый параметр теста: ручка берёт «сейчас» сама, и без
    # закрепления красный зависел бы от времени суток прогона.
    monkeypatch.setattr(
        "apps.master_api.views.dj_timezone.now", lambda: _at(10).astimezone(dt.timezone.utc)
    )

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        resp = client.get(
            reverse("master_api:dashboard"), HTTP_AUTHORIZATION=init_data_header("12345")
        )

    assert resp.status_code == 200, resp.content
    assert resp.json()["today_summary"]["next_free_window"] is None
