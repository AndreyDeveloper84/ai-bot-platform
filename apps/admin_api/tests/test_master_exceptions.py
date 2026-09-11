"""GET /api/v1/admin/masters/<id>/exceptions/ — показ назначенного (DRF-1240).

Четыре вещи, которые эти тесты держат:

* **исключения, недоступность и закрытия видны салону.** Сегодня они не
  показаны нигде: день мастера показывает сегодняшнюю рамку, кадр салона —
  сегодняшние смены, а «что уже назначено на неделю вперёд» не показывает
  никто;
* **«строк нет» и «строки есть, но я их не понял» — разные ответы.** Тот же
  запрет молчаливого ноля, что в кадре салона, и та же цена: пустота
  читается как «ничего не назначено», и салон спланирует день поверх отгула;
* **недоступный источник называется, а не рисуется пустыми списками.** По
  той же причине;
* **записи здесь нет, и вид говорит это прямо.** Все записывающие маршруты
  салонной поверхности SERVICE_READ_ONLY; §117 разрешает креденшел условно —
  сначала три проверки, потом использование. Экран не должен рисовать
  кнопок, которых сервер не примет.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonUnavailable
from apps.tenancy.models import Tenant

from .conftest import init_data_header

pytestmark = pytest.mark.django_db


def _exception_row(**overrides) -> dict:
    """Исключение в форме, которую разбирает живой ``schedule_frame``.

    Имена полей взяты не из докстринга, а из разбора, работающего на пилоте
    под включённым флагом: ``date``, ``is_working_day``, ``start_time``,
    ``end_time``.
    """

    row = {
        "id": "exc-1",
        "date": "2026-09-12",
        "is_working_day": True,
        "start_time": "12:00",
        "end_time": "16:00",
    }
    row.update(overrides)
    return row


def _time_off_row(**overrides) -> dict:
    row = {
        "id": "off-1",
        "start_at": "2026-09-13T10:00:00+03:00",
        "end_at": "2026-09-13T14:00:00+03:00",
        "reason": "учёба",
    }
    row.update(overrides)
    return row


def _closure_row(**overrides) -> dict:
    row = {
        "id": "cl-1",
        "date": "2026-09-14",
        "start_time": None,
        "end_time": None,
        "reason": "санитарный день",
    }
    row.update(overrides)
    return row


class _FakeSalonClient:
    def __init__(self, exceptions=None, time_off=None, closures=None, exc=None):
        self.exceptions = [_exception_row()] if exceptions is None else exceptions
        self.time_off = [_time_off_row()] if time_off is None else time_off
        self.closures = [_closure_row()] if closures is None else closures
        self.exc = exc
        self.calls: list[str] = []

    def list_schedule_exceptions(self, **kwargs):
        self.calls.append("exceptions")
        if self.exc:
            raise self.exc
        return self.exceptions

    def list_time_off(self, **kwargs):
        self.calls.append("time_off")
        if self.exc:
            raise self.exc
        return self.time_off

    def list_closures(self, **kwargs):
        self.calls.append("closures")
        if self.exc:
            raise self.exc
        return self.closures


@pytest.fixture
def ayla(monkeypatch, settings):
    """Флаг ВКЛЮЧЁН — живая конфигурация пилота (замер 09.09.2026)."""

    settings.BOOKING_VIA_AYLA_REST = True

    def use(**kwargs) -> _FakeSalonClient:
        client = _FakeSalonClient(**kwargs)
        # Адрес подмены — имя В МОЁМ модуле: он связывает ``get_salon_client``
        # на импорте, и подмена исходного имени до него не доедет.
        monkeypatch.setattr(
            "apps.admin_api.views_master_exceptions.get_salon_client", lambda: client
        )
        return client

    return use


@pytest.fixture
def synced_master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=931,
        external_updated_at=datetime.now(tz=dt_timezone.utc),
        name="Ольга Синхронная",
        ayla_user_id=uuid.uuid4(),
    )


def _url(master: CatalogMaster) -> str:
    return reverse("admin_api:master_exceptions", args=[str(master.id)])


def _get(client: Client, master: CatalogMaster, *, user_id: str = "5001", **params):
    return client.get(_url(master), params, HTTP_AUTHORIZATION=init_data_header(user_id))


class TestTheSalonSeesWhatIsAlreadyBooked:
    def test_all_three_lists_come_back(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        fake = ayla()

        resp = _get(client, synced_master)

        assert resp.status_code == 200
        body = resp.json()
        assert body["exceptions"]["state"] == "parsed"
        assert body["exceptions"]["rows"][0]["date"] == "2026-09-12"
        assert body["exceptions"]["rows"][0]["start"] == "12:00"
        assert body["time_off"]["rows"][0]["reason"] == "учёба"
        assert body["closures"]["rows"][0]["date"] == "2026-09-14"
        assert fake.calls == ["exceptions", "time_off", "closures"]

    def test_a_day_off_carries_no_hours(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        # «Не работаю» с часами — противоречие в одном ответе. Показать их
        # значило бы предложить время, которое тем же ответом объявлено
        # нерабочим.
        ayla(
            exceptions=[_exception_row(is_working_day=False, start_time="12:00", end_time="16:00")]
        )

        row = _get(client, synced_master).json()["exceptions"]["rows"][0]

        assert row["is_working_day"] is False
        assert row["start"] is None
        assert row["end"] is None

    def test_the_view_says_it_cannot_write(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        # Экран не должен рисовать кнопок, которых сервер не примет: все
        # записывающие маршруты салонной поверхности SERVICE_READ_ONLY, а
        # §117 разрешает креденшел условно — сначала три проверки.
        ayla()

        assert _get(client, synced_master).json()["writable"] is False

    def test_a_master_of_another_salon_is_not_found(
        self, client: Client, owner_bot_user: BotUser, other_tenant: Tenant, ayla
    ) -> None:
        ayla()
        foreign = CatalogMaster.all_tenants.create(
            tenant=other_tenant,
            external_id=932,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Чужая",
            ayla_user_id=uuid.uuid4(),
        )

        assert _get(client, foreign).status_code == 404


class TestAnEmptyListAndAnUnreadableOneAreDifferentAnswers:
    def test_no_rows_says_none(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(exceptions=[], time_off=[], closures=[])

        body = _get(client, synced_master).json()

        assert body["exceptions"]["state"] == "none"
        assert body["unreadable_lists"] == []

    def test_rows_that_do_not_parse_are_named_not_dropped(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        # Пустота прочиталась бы как «ничего не назначено», и салон
        # спланировал бы день поверх отгула.
        ayla(time_off=[{"from": "2026-09-13", "to": "2026-09-13"}])

        body = _get(client, synced_master).json()

        assert body["time_off"]["state"] == "unreadable"
        assert body["time_off"]["rows"] == []
        assert body["time_off"]["seen_fields"] == ["from", "to"]
        assert body["unreadable_lists"] == ["time_off"]

    def test_a_row_with_the_right_names_but_a_broken_date_is_not_dropped(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        # Ловушка тоньше чужих имён: имя ТО, значение мусор.
        ayla(exceptions=[_exception_row(date="послезавтра")])

        assert _get(client, synced_master).json()["exceptions"]["state"] == "unreadable"

    def test_closures_are_read_by_the_same_rule(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        # Форма строки закрытия известна только из докстринга, то есть это
        # пересказ. Тем важнее, чтобы непонятая строка называлась, а не
        # исчезала.
        ayla(closures=[{"closed_on": "2026-09-14"}])

        body = _get(client, synced_master).json()

        assert body["closures"]["state"] == "unreadable"
        assert body["unreadable_lists"] == ["closures"]


class TestTheSourceRefusesByName:
    def test_an_unavailable_source_is_named(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(exc=SalonUnavailable("upstream 502"))

        resp = _get(client, synced_master)

        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"

    def test_the_local_source_is_named_rather_than_pretended(
        self,
        client: Client,
        owner_bot_user: BotUser,
        synced_master: CatalogMaster,
        settings,
        monkeypatch,
    ) -> None:
        settings.BOOKING_VIA_AYLA_REST = False

        def _boom():  # pragma: no cover — вызов и есть то, чего быть не должно
            raise AssertionError("Ayla читается при выключенном флаге")

        monkeypatch.setattr("apps.admin_api.views_master_exceptions.get_salon_client", _boom)

        resp = _get(client, synced_master)

        assert resp.status_code == 503
        assert resp.json()["error"] == "frame_source_local_unsupported"

    def test_a_bad_range_is_refused_before_the_wire(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        fake = ayla()

        resp = _get(client, synced_master, **{"from": "2026-09-20", "to": "2026-09-10"})

        assert resp.status_code == 400
        assert fake.calls == []


class TestTheFrontDeskMayReadButNotChange:
    """§141 (решение владельца 11.09.2026) — и его отрицательная половина.

    Положительная: ресепшн ВИДИТ расписание мастеров своего салона, включая
    отгулы и изменения на день. До решения вид стоял под
    ``require_admin_role``, то есть уже решённого, и оставить его таким
    значило бы держать права уже решения — молча, по инерции.

    Отрицательная важнее: декоратор обязан ЗАПРЕЩАТЬ запись так же явно, как
    разрешает чтение. Иначе через месяц его переиспользуют на изменяющей
    ручке, и расширение прав произойдёт по НАЗВАНИЮ декоратора, а не по
    решению владельца.
    """

    def test_the_receptionist_sees_what_is_assigned(
        self,
        client: Client,
        owner_bot_user: BotUser,
        receptionist_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
    ) -> None:
        # Владелец в фикстуре нужен не для прав, а чтобы у тенанта был тот,
        # чьи права Ayla проверяет при чтении.
        ayla()

        assert _get(client, synced_master, user_id="5003").status_code == 200

    def test_the_receptionist_cannot_reach_it_with_an_unsafe_method(
        self,
        client: Client,
        owner_bot_user: BotUser,
        receptionist_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
    ) -> None:
        # Запрет держат ДВОЕ: декоратор пускает ресепшн только на безопасном
        # методе, и вид отдельно объявлен GET-only. Снятие любого одного
        # оставляет второго — поэтому проверяется исход, а не механизм:
        # небезопасный метод НЕ должен пройти.
        ayla()

        resp = client.post(
            _url(synced_master),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5003"),
        )

        assert resp.status_code in (403, 405), resp.status_code

    def test_a_master_still_cannot_read_it(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master_only_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
    ) -> None:
        # Положительный контроль к расширению: открыли ресепшену — не значит
        # открыли всем. Без этого теста «ресепшн видит» зеленело бы и на
        # виде, снявшем проверку роли вовсе.
        ayla()

        assert (
            _get(
                client,
                synced_master,
                user_id=str(master_only_bot_user.channel_user_id),
            ).status_code
            == 403
        )
