"""GET/POST /api/v1/admin/masters/<id>/schedule/ — §83, правило 2.

Владелец салона видит рабочие часы мастера и подтверждает их. Эти тесты
держат обе половины и три границы, каждая из которых стоила бы отдельного
дефекта:

* показ идёт из ТОГО ЖЕ источника, с которого снимается отпечаток, — иначе
  владелица заверяет одни часы, а клиенту продают другие;
* недоступный источник отвечает НАЗВАННОЙ причиной, а не пустым
  расписанием: пустой список читался бы как «мастер не работает никогда»;
* подтверждается ТО, ЧТО ВИДЕЛИ: если часы изменились между показом и
  нажатием, ответ ``stale_view``, а не молчаливое подтверждение новых.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonUnavailable
from apps.tenancy.models import Tenant

from .conftest import init_data_header

pytestmark = pytest.mark.django_db


def _wire_week(**overrides) -> list[dict]:
    rows = [
        {
            "day_of_week": i,
            "day_name": "",
            "is_working_day": i != 6,
            "start_time": "10:00" if i != 6 else None,
            "end_time": "19:00" if i != 6 else None,
            "break_start": None,
            "break_end": None,
        }
        for i in range(7)
    ]
    for day, patch in overrides.items():
        rows[int(day.removeprefix("day"))].update(patch)
    return rows


class _FakeSalonClient:
    def __init__(self, template, exc=None, exceptions=None, time_off=None):
        self.template = template
        self.exc = exc
        self.exceptions = exceptions or []
        self.time_off = time_off or []

    def get_master_schedule(self, **kwargs):
        if self.exc:
            raise self.exc
        return self.template

    # ``build_schedule`` читает кадр целиком: недельный шаблон, исключения по
    # датам и отгулы. Заглушка обязана знать все три — иначе тест падает на
    # стенде и говорит про `AttributeError`, а не про предмет.
    def list_schedule_exceptions(self, **kwargs):
        if self.exc:
            raise self.exc
        return list(self.exceptions)

    def list_time_off(self, **kwargs):
        if self.exc:
            raise self.exc
        return list(self.time_off)


@pytest.fixture
def ayla(monkeypatch, settings):
    """Флаг ВКЛЮЧЁН — живая конфигурация пилота (замер 09.09.2026)."""

    settings.BOOKING_VIA_AYLA_REST = True

    def use(template, exc=None, exceptions=None, time_off=None):
        client = _FakeSalonClient(template, exc, exceptions, time_off)
        # Два адреса, а не один, и это не перестраховка. Мой сервис
        # импортирует ``get_salon_client`` ВНУТРИ функции — ему довольно
        # исходного имени. ``schedule_frame`` импортирует его на уровне
        # модуля (строка 48), то есть имя связано на импорте, и подмена
        # исходного до него не доходит. Пропусти второй адрес — тест
        # молча пойдёт в настоящего клиента.
        monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: client)
        monkeypatch.setattr(
            "apps.master_api.services.schedule_frame.get_salon_client", lambda: client
        )
        return client

    return use


@pytest.fixture
def synced_master(tenant: Tenant) -> CatalogMaster:
    """Мастер в форме, в которой он приезжает синхронизацией."""

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=901,
        external_updated_at=datetime.now(tz=dt_timezone.utc),
        name="Ольга Синхронная",
        ayla_user_id=uuid.uuid4(),
    )


def _url(master: CatalogMaster) -> str:
    return reverse("admin_api:master_schedule", args=[str(master.id)])


def _confirm_url(master: CatalogMaster) -> str:
    return reverse("admin_api:master_schedule_confirm", args=[str(master.id)])


def _get(client: Client, master: CatalogMaster, *, user_id: str = "5001"):
    return client.get(_url(master), HTTP_AUTHORIZATION=init_data_header(user_id))


def _confirm(client: Client, master: CatalogMaster, body: dict, *, user_id: str = "5001"):
    return client.post(
        _confirm_url(master),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


class TestTheOwnerSeesTheHoursSheIsAskedToVouchFor:
    def test_the_week_comes_back_with_all_seven_days(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(_wire_week())

        resp = _get(client, synced_master)

        assert resp.status_code == 200
        schedule = resp.json()["schedule"]
        assert schedule["source"] == "ayla"
        assert len(schedule["days"]) == 7
        assert schedule["has_working_day"] is True
        assert [d["day_of_week"] for d in schedule["days"]] == list(range(7))

    def test_the_break_is_shown_because_it_is_part_of_the_hours(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Перерыв виден на экране — его же несёт отпечаток.

        Показать часы без перерыва значило бы просить заверить неполное:
        владелица не увидела бы того, что подписывает.
        """

        ayla(_wire_week(day0={"break_start": "13:00", "break_end": "14:00"}))

        days = _get(client, synced_master).json()["schedule"]["days"]

        assert days[0]["break_start"] == "13:00"
        assert days[0]["break_end"] == "14:00"

    def test_an_unconfirmed_master_says_so(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(_wire_week())

        confirmation = _get(client, synced_master).json()["schedule"]["confirmation"]

        assert confirmation["confirmed_at"] is None
        assert confirmation["confirmed_by"] is None
        assert confirmation["is_current"] is False
        assert confirmation["block"] is None  # нажимать можно

    def test_an_admin_may_look(
        self, client: Client, admin_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Смотреть может и админ: прятать состояние от того, кто видит
        ростер, дороже, чем показать."""

        ayla(_wire_week())
        assert _get(client, synced_master, user_id="5002").status_code == 200


class TestAnUnreachableSourceIsNamedNotDrawnAsEmpty:
    def test_ayla_down_answers_503_by_name(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Пустое расписание вместо отказа отправило бы владелицу чинить
        график, с которым всё в порядке."""

        ayla([], exc=SalonUnavailable("upstream down"))

        resp = _get(client, synced_master)

        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"

    def test_a_short_wire_answer_is_our_failure_not_her_schedule(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(_wire_week()[:4])

        resp = _get(client, synced_master)

        assert resp.status_code == 502
        assert resp.json()["error"] == "wire_incomplete"


class TestConfirmingSignsWhatWasSeen:
    def test_the_owner_confirms_and_the_row_records_it(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(_wire_week())
        seen = _get(client, synced_master).json()["schedule"]["confirmation"]["fingerprint"]

        resp = _confirm(client, synced_master, {"fingerprint": seen})

        assert resp.status_code == 200
        confirmation = resp.json()["schedule"]["confirmation"]
        assert confirmation["is_current"] is True
        assert confirmation["confirmed_by"]["id"] == str(owner_bot_user.id)

        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is not None
        assert synced_master.schedule_fingerprint == seen

    def test_hours_changed_while_she_looked_refuses_with_a_name(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """``stale_view`` — половина правила 2, без которой оно пустое.

        Сервис читает источник заново в момент нажатия, иначе подписывал
        бы вчерашний экран. Но без сверки с увиденным он подписал бы
        часы, которых владелица НЕ видела. Нужны обе половины.
        """

        ayla(_wire_week())
        seen = _get(client, synced_master).json()["schedule"]["confirmation"]["fingerprint"]

        ayla(_wire_week(day0={"end_time": "22:00"}))
        resp = _confirm(client, synced_master, {"fingerprint": seen})

        assert resp.status_code == 409
        assert resp.json()["error"] == "stale_view"
        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is None

    def test_confirming_without_saying_what_is_refused(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Подтверждение без указания, ЧТО подтверждают, — это «кто-то
        когда-то нажал». Умолчания здесь нет намеренно."""

        ayla(_wire_week())

        resp = _confirm(client, synced_master, {})

        assert resp.status_code == 400
        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is None

    def test_a_week_with_no_working_day_cannot_be_confirmed(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Правило 7 доезжает до ручки, а не остаётся в сервисе."""

        empty = _wire_week()
        for row in empty:
            row.update(is_working_day=False, start_time=None, end_time=None)
        ayla(empty)

        shown = _get(client, synced_master).json()["schedule"]
        assert shown["confirmation"]["block"] == "no_working_day"

        resp = _confirm(
            client, synced_master, {"fingerprint": shown["confirmation"]["fingerprint"]}
        )

        assert resp.status_code == 409
        assert resp.json()["error"] == "no_working_day"
        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is None

    def test_an_admin_may_not_confirm(
        self,
        client: Client,
        owner_bot_user: BotUser,
        admin_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
    ) -> None:
        """Подтверждение допускает человека к продаже — это владелица.

        Тот же довод, что у подтверждения приглашения: право смотреть и
        право допускать к витрине — не одно право.
        """

        ayla(_wire_week())
        seen = _get(client, synced_master).json()["schedule"]["confirmation"]["fingerprint"]

        resp = _confirm(client, synced_master, {"fingerprint": seen}, user_id="5002")

        assert resp.status_code == 403
        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is None


class TestTheConfirmationIsAboutTheseHoursNotAnyHours:
    def test_after_the_hours_change_the_screen_says_it_is_no_longer_current(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Столбец на месте, а «актуально» — уже нет.

        Это то самое различие, без которого отпечаток бесполезен:
        «подтверждено когда-то» ≠ «подтверждено для этой версии часов».
        """

        ayla(_wire_week())
        seen = _get(client, synced_master).json()["schedule"]["confirmation"]["fingerprint"]
        _confirm(client, synced_master, {"fingerprint": seen})

        ayla(_wire_week(day2={"start_time": "12:00"}))
        confirmation = _get(client, synced_master).json()["schedule"]["confirmation"]

        assert confirmation["confirmed_at"] is not None
        assert confirmation["is_current"] is False


class TestAnotherSalonsMasterIsNotVisibleHere:
    def test_a_cross_tenant_master_is_not_found(
        self, client: Client, owner_bot_user: BotUser, other_tenant: Tenant, ayla
    ) -> None:
        """Чужой мастер отвечает «нет такого», а не расписанием."""

        foreign = CatalogMaster.all_tenants.create(
            tenant=other_tenant,
            external_id=902,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Чужая",
            ayla_user_id=uuid.uuid4(),
        )
        ayla(_wire_week())

        assert _get(client, foreign).status_code == 404


# ─── день мастера для салона (DRF-1237, срез A1) ─────────────────────────────


def _day_url(master: CatalogMaster) -> str:
    return reverse("admin_api:master_day_schedule", args=[str(master.id)])


def _get_day(client: Client, master: CatalogMaster, *, user_id: str = "5001", **params):
    return client.get(_day_url(master), params, HTTP_AUTHORIZATION=init_data_header(user_id))


class TestTheSalonSeesTheMastersDayThroughTheSameCalculator:
    """Четвёртого вычислителя «свободного времени» в продукте не заводим.

    Сегодня их три — резолвер на пути записи, ``build_schedule`` на экране
    мастера, слоты каталога, — и они между собой расходятся (§117,
    DRF-1637). Салонная вкладка подключается к существующему, а не считает
    сама и тем более не считает на клиенте: последнее — «клиент выдумывает
    доступность» (§17).
    """

    def test_the_day_comes_back_with_the_frame_not_just_the_visits(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Именно рамка отличает этот ответ от «дня салона».

        ``GET /api/v1/admin/day/`` отдаёт визиты и только визиты. Смены,
        исключений и отгулов там нет, и построить из него «рабочий день
        мастера» нельзя — это и есть причина, по которой вид существует.
        """

        ayla(_wire_week())

        body = _get_day(client, synced_master).json()

        assert body["from"] and body["to"] and body["tenant_tz"]
        day = body["days"][0]
        for field in (
            "date",
            "is_off_day",
            "working_hours",
            "bookings",
            "blocks",
            "free_windows",
            "conflicts",
        ):
            assert field in day, field

    def test_a_master_of_another_salon_is_not_found(
        self, client: Client, owner_bot_user: BotUser, other_tenant: Tenant, ayla
    ) -> None:
        """Чужой мастер отвечает «нет такого», а не его расписанием.

        Обязательно, а не осторожно: докстринг ``build_schedule`` говорит
        прямо — «cross-master scoping happens at the auth layer; this helper
        trusts the input». Сервис на скоуп не смотрит и смотреть не должен,
        значит смотреть обязаны мы.
        """

        foreign = CatalogMaster.all_tenants.create(
            tenant=other_tenant,
            external_id=911,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Чужая",
            ayla_user_id=uuid.uuid4(),
        )
        ayla(_wire_week())

        assert _get_day(client, foreign).status_code == 404

    def test_the_range_limits_come_from_the_calculator_not_from_here(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Два предела на один расчёт разъехались бы молча."""

        from apps.master_api.services.schedule import MAX_RANGE_DAYS

        ayla(_wire_week())
        start = date(2026, 9, 10)
        too_far = start + timedelta(days=MAX_RANGE_DAYS)

        resp = _get_day(
            client,
            synced_master,
            **{"from": start.isoformat(), "to": too_far.isoformat()},
        )

        assert resp.status_code == 400
        assert str(MAX_RANGE_DAYS) in resp.json()["detail"]

    def test_an_unreachable_source_is_named_not_drawn_as_a_free_day(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Пустой день читался бы как «мастер свободен весь день»."""

        ayla([], exc=SalonUnavailable("upstream down"))

        resp = _get_day(client, synced_master)

        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"


class TestTheKnownBlindnessIsPinnedNotInherited:
    """DRF-1638 — обеденный перерыв показывается свободным временем.

    Это **negative-baseline artifact** (§114): тест фиксирует НЕ то, что
    перерыв обрабатывается, а то, что НЕ обрабатывается. Он обязан
    покраснеть в день, когда обработку добавят, и привести читателя сюда —
    а не быть удалённым при закрытии задачи.

    Цепь замкнута с обеих сторон и на ЖИВОЙ ветке (флаг на пилоте включён):

    * провод недельного шаблона Ayla несёт ``break_start`` / ``break_end``;
    * ``schedule_frame.FrameHours`` несёт три поля из семи и перерыв роняет;
    * ``_compute_free_windows`` вычитает записи и блоки — перерыв не то и не
      другое;
    * каталог при этом считает перерыв занятым и на чтении, и на записи
      (``slot_builder``), то есть запись на обед будет отклонена.

    Экспозиция на 10.09.2026: 63 строки часов у девяти мастеров, перерыв не
    заполнен ни у одного. Расхождение не спит — оно ждёт первой строки.
    """

    def test_the_lunch_break_is_known_to_leak_into_free_windows(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Ответ с перерывом и без перерыва СОВПАДАЕТ — вот и вся утечка.

        Сравнение двух ответов, а не утверждение о покрытии: тест, который
        проверяет «окно накрывает 13:00–14:00», прошёл бы и на расписании,
        где обеда просто нет. Здесь вход отличается ровно перерывом, и
        равенство выходов — это и есть измеренный дефект, а не догадка.

        В день, когда DRF-1638 закроют, эти два ответа обязаны разойтись, и
        тест покраснеет здесь.
        """

        monday = date(2026, 9, 7)
        params = {"from": monday.isoformat(), "to": monday.isoformat()}

        ayla(_wire_week())
        without_break = _get_day(client, synced_master, **params).json()

        ayla(_wire_week(day0={"break_start": "13:00", "break_end": "14:00"}))
        with_break = _get_day(client, synced_master, **params).json()

        windows_without = without_break["days"][0]["free_windows"]
        windows_with = with_break["days"][0]["free_windows"]

        # Положительная стража: окна вообще посчитаны. Без неё равенство
        # выполнилось бы и на двух пустых списках — то есть на сломанном
        # стенде, а не на дефекте.
        assert windows_without, "окна не посчитаны — проверять нечего"

        assert windows_with == windows_without, (
            "DRF-1638 закрыт — перерыв больше не протекает в свободные окна. "
            "Это ХОРОШАЯ новость: снимите этот тест и проверьте, что вкладка "
            "салона перестала предлагать время, которое запись отклоняет."
        )


class TestTheFrontDeskSeesTheMastersDay:
    """§141 (решение владельца 11.09.2026): ресепшн видит — на чтение.

    До решения вид дня стоял под ``require_admin_role``, то есть УЖЕ
    решённого. Вопрос был вынесен владельцу (DRF-1640) именно чтобы права
    не определялись выбором декоратора; ответ получен, и вид приведён к нему.

    Отрицательная половина важнее положительной: расширение обязано
    оставаться чтением. Иначе через месяц тот же декоратор окажется на
    изменяющей ручке, и права вырастут по названию, а не по решению.
    """

    def test_the_receptionist_sees_the_day(
        self,
        client: Client,
        owner_bot_user: BotUser,
        receptionist_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
    ) -> None:
        ayla(_wire_week())

        assert _get_day(client, synced_master, user_id="5003").status_code == 200

    def test_the_receptionist_cannot_confirm_the_schedule(
        self,
        client: Client,
        owner_bot_user: BotUser,
        receptionist_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
    ) -> None:
        # Вот где расширение обязано остановиться. Подтверждение делает
        # мастера продаваемым клиенту — это решение владелицы салона, и
        # ресепшн его не принимает. Видеть график и заверять его — разные
        # права, и открытие первого не открывает второго.
        ayla(_wire_week())

        resp = client.post(
            _confirm_url(synced_master),
            data=json.dumps({"fingerprint": "whatever"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5003"),
        )

        assert resp.status_code == 403, resp.status_code

    def test_the_day_view_refuses_an_unsafe_method_from_the_front_desk(
        self,
        client: Client,
        owner_bot_user: BotUser,
        receptionist_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
    ) -> None:
        ayla(_wire_week())

        resp = client.post(
            _day_url(synced_master),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5003"),
        )

        assert resp.status_code in (403, 405), resp.status_code
