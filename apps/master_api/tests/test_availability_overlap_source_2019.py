"""Заявка мастера на отсутствие сверяется с живым расписанием, а не с копией (DRF-2019).

`request_availability_change` (`services/schedule.py:861`) отказывает `overlap`,
когда запрошенное окно пересекает уже одобренное исключение. Исключения он берёт
из **локальной** `ScheduleException` (`:914-919`) независимо от
`BOOKING_VIA_AYLA_REST` — в отличие от экрана расписания, готовности и (после
DRF-2014) дашборда, которые ходят через `services/schedule_frame.py:152
load_day_frame`.

**Почему это не видно сегодня.** Замер главного окна (хост `ruvds-o1mqo`, база
`ayla-bot-staging-postgres-1`, 15.09.2026 ~23:20 UTC, `BEGIN READ ONLY`; у
наблюдения есть срок годности): `scheduling_scheduleexception` — **0 строк**,
`scheduling_schedulechangerequest` — 0. Путь молчит **по данным, а не по
устройству**: первая же строка в копии — и кабинет начнёт решать по ней.
Поэтому каждый узел здесь **сеет своё условие сам**; зелень на пустой таблице
доказательством не является.

Закрепляется:

* флаг включён — решает исключение **каталога**, а устаревшая локальная строка
  не решает ничего;
* граница частичного исключения (`CUSTOM_HOURS`) считается по тому же дню после
  смены формы: у каталожного `FrameException` (`schedule_frame.py:111`) нет
  поля `date` — дата приходит ключом словаря, а сегодняшний цикл читает
  `exc.date` трижды;
* флаг выключен — прежняя локальная сверка, и в каталог не ходим;
* каталог не прочитан — отказ, а не создание заявки против расписания, которого
  мы не видели;
* область видимости запроса — свой тенант: чужая строка на ту же дату ответ не
  меняет.

Копию `apps/scheduling` этот лист не трогает (X5), клиентский путь — X6,
третьего состояния не вводит (X7).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonNotConfigured
from apps.master_api.services import schedule as sched
from apps.master_api.services import schedule_frame
from apps.scheduling.models import ScheduleChangeRequest, ScheduleException
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
#: 21.05.2026 — четверг, как у соседних тестов кабинета.
DAY = dt.date(2026, 5, 21)
#: Имя причины в логе: наружу одно имя отказа, внутрь — названная причина (X7).
FRAME_UNREADABLE_LOG = "master.availability.frame_unreadable"


def _utc(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 5, 21, hour, minute, tzinfo=MSK).astimezone(dt.timezone.utc)


def _owner_staff(tenant: Tenant) -> BotUser:
    """Активный владелец — от его имени читается рамка каталога."""
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"owner-{uuid.uuid4().hex[:8]}",
        ayla_user_id=str(uuid.uuid4()),
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER)
    return bot_user


def _exception_row(*, is_working_day: bool, start: str | None, end: str | None) -> list[dict]:
    """Строка исключения так, как её отдаёт каталог."""
    return [
        {
            "id": "e-2019",
            "date": DAY.isoformat(),
            "is_working_day": is_working_day,
            "start_time": start,
            "end_time": end,
            "break_start": None,
            "break_end": None,
            "note": "",
        }
    ]


class _FakeSalonClient:
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
        if self._exc:
            raise self._exc
        return self._exceptions

    def list_time_off(self, **kwargs):
        self.calls.append("time_off")
        return self._time_off


class _ForbiddenSalonClient:
    def __getattr__(self, name: str):
        def _boom(**kwargs):
            raise AssertionError(f"каталог не должен опрашиваться при флаге OFF: {name}")

        return _boom


def _use_client(monkeypatch, client) -> None:
    monkeypatch.setattr(schedule_frame, "get_salon_client", lambda: client)


@pytest.fixture
def catalog_master(tenant: Tenant, accepted_master: CatalogMaster) -> CatalogMaster:
    CatalogMaster.all_tenants.filter(pk=accepted_master.pk).update(
        catalog_specialist_id=accepted_master.pk
    )
    accepted_master.refresh_from_db()
    _owner_staff(tenant)
    return accepted_master


def _seed_local_day_off(tenant: Tenant, master: CatalogMaster, *, day: dt.date = DAY) -> None:
    """Устаревшая строка локальной копии: полный выходной."""
    ScheduleException.all_tenants.create(
        tenant=tenant,
        master=master,
        date=day,
        type=ScheduleException.Type.DAY_OFF,
    )


def _request(master: CatalogMaster, actor: BotUser, *, start: dt.datetime, end: dt.datetime):
    return sched.request_availability_change(
        master,
        start=start,
        end=end,
        reason_class=ScheduleChangeRequest.ReasonClass.PERSONAL,
        reason_text="",
        actor=actor,
        now=_utc(9),
    )


# ── источник решения ───────────────────────────────────────────────────────


def test_catalog_exception_blocks_the_request_when_the_flag_is_on(
    tenant: Tenant, catalog_master: CatalogMaster, bot_user: BotUser, monkeypatch
) -> None:
    """Каталог говорит «в этот день выходной» — заявка отклоняется, хотя копия пуста."""
    assert not ScheduleException.all_tenants.filter(master=catalog_master).exists()
    fake = _FakeSalonClient(exceptions=_exception_row(is_working_day=False, start=None, end=None))
    _use_client(monkeypatch, fake)

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        with pytest.raises(sched.AvailabilityRequestError) as exc:
            _request(catalog_master, bot_user, start=_utc(12), end=_utc(13))

    assert exc.value.slug == "overlap"


def test_stale_local_exception_does_not_block_when_the_flag_is_on(
    tenant: Tenant, catalog_master: CatalogMaster, bot_user: BotUser, monkeypatch
) -> None:
    """Июльская копия решать не должна: в каталоге этого исключения нет."""
    _seed_local_day_off(tenant, catalog_master)
    _use_client(monkeypatch, _FakeSalonClient(exceptions=[]))

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        req = _request(catalog_master, bot_user, start=_utc(12), end=_utc(13))

    assert req.status == ScheduleChangeRequest.Status.PENDING


def test_custom_hours_partial_window_boundary_survives_the_frame(
    tenant: Tenant, catalog_master: CatalogMaster, bot_user: BotUser, monkeypatch
) -> None:
    """Частичное исключение 12:00–16:00: 15:30–16:30 пересекает, 16:00–17:00 — нет.

    У каталожного `FrameException` нет поля `date` — дата приходит ключом
    словаря, а сегодняшний цикл читает `exc.date`. Перенос смысла, а не
    переименование: граница обязана остаться на том же дне.
    """
    _use_client(
        monkeypatch,
        _FakeSalonClient(
            exceptions=_exception_row(is_working_day=True, start="12:00", end="16:00")
        ),
    )

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        with pytest.raises(sched.AvailabilityRequestError) as exc:
            _request(catalog_master, bot_user, start=_utc(15, 30), end=_utc(16, 30))
        assert exc.value.slug == "overlap"

        touching = _request(catalog_master, bot_user, start=_utc(16), end=_utc(17))

    assert touching.status == ScheduleChangeRequest.Status.PENDING


def test_non_overlapping_window_is_still_created(
    tenant: Tenant, catalog_master: CatalogMaster, bot_user: BotUser, monkeypatch
) -> None:
    """Положительная стража: «всё пересекает» не пройдёт — исключение на другой день."""
    _use_client(
        monkeypatch,
        _FakeSalonClient(
            exceptions=[
                {
                    "id": "e-other",
                    "date": (DAY + dt.timedelta(days=3)).isoformat(),
                    "is_working_day": False,
                    "start_time": None,
                    "end_time": None,
                    "break_start": None,
                    "break_end": None,
                    "note": "",
                }
            ]
        ),
    )

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        req = _request(catalog_master, bot_user, start=_utc(12), end=_utc(13))

    assert req.status == ScheduleChangeRequest.Status.PENDING


# ── переключатель работает в обе стороны ───────────────────────────────────


def test_local_source_unchanged_when_the_flag_is_off(
    tenant: Tenant, catalog_master: CatalogMaster, bot_user: BotUser, monkeypatch
) -> None:
    """Флаг выключен — решает копия, и каталог не опрашивается вовсе."""
    _seed_local_day_off(tenant, catalog_master)
    _use_client(monkeypatch, _ForbiddenSalonClient())

    with override_settings(BOOKING_VIA_AYLA_REST=False):
        with pytest.raises(sched.AvailabilityRequestError) as exc:
            _request(catalog_master, bot_user, start=_utc(12), end=_utc(13))

    assert exc.value.slug == "overlap"


def test_catalog_is_consulted_when_the_flag_is_on(
    tenant: Tenant, catalog_master: CatalogMaster, bot_user: BotUser, monkeypatch
) -> None:
    """Положительный близнец к узлу выше: «не зван никогда» и «не зван пока» иначе неразличимы."""
    fake = _FakeSalonClient(exceptions=[])
    _use_client(monkeypatch, fake)

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        _request(catalog_master, bot_user, start=_utc(12), end=_utc(13))

    assert "exceptions" in fake.calls


def test_unreadable_frame_refuses_instead_of_allowing(
    tenant: Tenant, catalog_master: CatalogMaster, bot_user: BotUser, monkeypatch, caplog
) -> None:
    """Рамку не прочитали — отказ, а не заявка против невидимого расписания.

    Создать заявку здесь значило бы выдать разрешение по незнанию. Наружу одно
    имя отказа (третьего состояния не вводим — X7), внутрь — названная причина.
    """
    _use_client(monkeypatch, _FakeSalonClient(exc=SalonNotConfigured("no owner staff")))

    with override_settings(BOOKING_VIA_AYLA_REST=True), caplog.at_level(logging.INFO):
        with pytest.raises(sched.AvailabilityRequestError) as exc:
            _request(catalog_master, bot_user, start=_utc(12), end=_utc(13))

    assert exc.value.slug == "schedule_unavailable"
    named = [r.getMessage() for r in caplog.records if FRAME_UNREADABLE_LOG in r.getMessage()]
    assert named, "причина не названа в логе"
    assert any("reason=SalonNotConfigured" in m for m in named), named
    assert not ScheduleChangeRequest.all_tenants.filter(master=catalog_master).exists()


# ── область видимости ──────────────────────────────────────────────────────


def test_another_tenants_exception_does_not_change_the_answer(
    tenant: Tenant, catalog_master: CatalogMaster, bot_user: BotUser, monkeypatch
) -> None:
    """Запрос сверяется по своему тенанту.

    Сегодня это уже так: фильтр пришпиливает и `tenant_id`, и `master_id`
    (`services/schedule.py:915-916`), а строка мастера принадлежит ровно одному
    тенанту (`_MirrorBase`, `apps/catalog/models.py:59-62`). Узел зелёный с
    самого начала — он стоит, чтобы снятие `tenant_id=` из фильтра краснело.
    """
    other = Tenant.objects.create(slug="other-2019", name="Чужой салон", timezone="Europe/Moscow")
    other_master = CatalogMaster.all_tenants.create(
        tenant=other,
        external_id=20191,
        external_updated_at=dt.datetime.now(tz=dt.timezone.utc),
        name="Чужой мастер",
    )
    _seed_local_day_off(other, other_master)
    _use_client(monkeypatch, _FakeSalonClient(exceptions=[]))

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        req = _request(catalog_master, bot_user, start=_utc(12), end=_utc(13))

    assert req.status == ScheduleChangeRequest.Status.PENDING
