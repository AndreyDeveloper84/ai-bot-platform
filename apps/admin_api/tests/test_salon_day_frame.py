"""GET /api/v1/admin/day/frame/ — кадр салонного дня (DRF-1237, срез A2).

Четыре вещи, которые эти тесты держат:

* **весь салон приезжает ОДНИМ вызовом.** Прежняя дорога — ``build_schedule``
  на каждого мастера — стоит три REST-вызова в Ayla на человека; за салон это
  двенадцать на одно открытие экрана. Счётчик вызовов здесь не украшение: без
  него возврат к поштучному чтению пройдёт незамеченным;
* **записи отсюда НЕ берутся.** Ayla несёт ``bookings`` в том же ответе, и
  взять их было бы на строку короче. Нельзя: визиты читает зеркало, и второй
  источник записей на одном экране — это ровно то расхождение, ради
  недопущения которого ``salon_day`` вообще читает зеркало;
* **«строк нет» и «строки есть, но я их не понял» — разные ответы.** Это
  сердце среза. Перерывов на пилоте не завёл никто, значит форма непустой
  строки ``breaks`` не проверена ничем; если неопознанная строка будет
  отвечать пустотой, первый салон с обедом получит перерыв показанным
  рабочим временем;
* **источник отказывает НАЗВАННОЙ причиной.** Пустой день читается как «никто
  сегодня не работает», и администратор пойдёт чинить график, с которым всё в
  порядке.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonUnavailable

from .conftest import init_data_header

pytestmark = pytest.mark.django_db


def _master_row(**overrides) -> dict:
    """Мастер в той форме, в какой его вернул пилот 10.09.2026.

    Поля и их имена — с провода (``scripts/salon_day_shape_probe.sh``), а не
    придуманные: ``working_intervals`` с ``start_local`` / ``end_local``,
    ``breaks`` и ``absences`` присутствуют и пусты.
    """

    row = {
        "specialist_id": "sp-1",
        "display_name": "Ольга",
        "is_working_day": True,
        "schedule_source": "ayla",
        "schedule_note": None,
        "timezone_name": "Europe/Moscow",
        "working_intervals": [{"start_local": "10:00", "end_local": "19:00"}],
        "breaks": [],
        "absences": [],
        "bookings": [],
    }
    row.update(overrides)
    return row


def _day_payload(*masters: dict, date: str = "2026-09-10") -> dict:
    return {
        "date": date,
        "generated_at": f"{date}T06:00:00+03:00",
        "closures": [],
        "summary": {},
        "masters": list(masters) or [_master_row()],
    }


class _FakeSalonClient:
    """Считает вызовы: «одним запросом» — утверждение, которое надо стеречь."""

    def __init__(self, payload: dict, exc: Exception | None = None) -> None:
        self.payload = payload
        self.exc = exc
        self.calls: list[dict] = []

    def get_day(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.payload


@pytest.fixture
def ayla(monkeypatch, settings):
    """Флаг ВКЛЮЧЁН — живая конфигурация пилота (замер 09.09.2026)."""

    settings.BOOKING_VIA_AYLA_REST = True

    def use(payload: dict, exc: Exception | None = None) -> _FakeSalonClient:
        client = _FakeSalonClient(payload, exc)
        # Адрес подмены — имя В МОЁМ модуле: ``views_salon_frame`` связывает
        # ``get_salon_client`` на импорте, и подмена исходного имени до него
        # не доедет. На этом уже попался срез A1; здесь адрес один, потому
        # что связывание одно.
        monkeypatch.setattr("apps.admin_api.views_salon_frame.get_salon_client", lambda: client)
        return client

    return use


def resp_text(body: dict) -> str:
    """Весь ответ строкой — чтобы искать утечку записи где угодно, не только
    в том поле, где её ждёшь."""

    return json.dumps(body, ensure_ascii=False)


def _url() -> str:
    return reverse("admin_api:salon_day_frame")


def _get(client: Client, *, user_id: str = "5001", **params):
    return client.get(_url(), params, HTTP_AUTHORIZATION=init_data_header(user_id))


class TestTheWholeSalonArrivesInOneCall:
    def test_every_masters_shift_comes_back_from_a_single_upstream_call(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        fake = ayla(
            _day_payload(
                _master_row(specialist_id="sp-1", display_name="Ольга"),
                _master_row(
                    specialist_id="sp-2",
                    display_name="Денис",
                    working_intervals=[{"start_local": "12:00", "end_local": "20:00"}],
                ),
            )
        )

        resp = _get(client)

        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-09-10"
        assert [m["display_name"] for m in body["masters"]] == ["Ольга", "Денис"]
        assert body["masters"][1]["working_intervals"] == {
            "state": "parsed",
            "rows": [{"start": "12:00", "end": "20:00"}],
            "seen_fields": ["end_local", "start_local"],
        }
        # Предмет: ОДИН вызов на весь салон, а не по одному на мастера.
        assert len(fake.calls) == 1

    def test_the_visits_are_not_taken_from_this_answer(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # Ayla кладёт записи в тот же ответ. Взять их отсюда значило бы
        # завести второй источник визитов рядом с зеркалом — и вернуть то
        # расхождение, из-за которого ``salon_day`` читает зеркало.
        ayla(
            _day_payload(
                _master_row(
                    bookings=[{"appointment_id": "b-1", "start_at": "2026-09-10T11:00:00Z"}]
                )
            )
        )

        body = _get(client).json()

        # Присутствие впереди отсутствия. Пустой ответ — сломанный вид,
        # отказ прав, пустой список — даёт «записей нет» ровно так же, как
        # правильный, и тест зеленел бы на нерабочем. Утверждать надо на ТОМ
        # ЖЕ выражении, из которого потом читается отсутствие.
        assert body["masters"], "в ответе нет ни одного мастера — проверять нечего"
        assert body["masters"][0]["display_name"] == "Ольга"

        assert "bookings" not in body["masters"][0]
        assert "b-1" not in resp_text(body)

    def test_the_date_comes_from_the_answer_not_from_our_clock(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # «Сегодня» считает салон по своему часовому поясу. Подставлять сюда
        # своё значило бы спорить с ним о том, какой сегодня день.
        ayla(_day_payload(date="2026-12-31"))

        assert _get(client).json()["date"] == "2026-12-31"


class TestAnEmptyListAndAnUnreadableOneAreDifferentAnswers:
    """Сердце среза: молчаливый ноль запрещён.

    Перерывов на пилоте не завёл никто (63 строки часов у девяти мастеров,
    ``break_start`` заполнен у нуля), поэтому форма непустой строки ``breaks``
    не проверена ничем. Пока это так, «строк нет» и «строки есть, но я их не
    понял» обязаны звучать по-разному — иначе первый салон с обедом получит
    перерыв, показанный рабочим временем.
    """

    def test_a_present_but_empty_list_says_none(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(_day_payload(_master_row(breaks=[])))

        breaks = _get(client).json()["masters"][0]["breaks"]

        assert breaks["state"] == "none"
        assert breaks["rows"] == []

    def test_a_missing_key_says_absent_not_none(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # Отсутствие ключа — это «контракт разошёлся», а не «сегодня пусто».
        row = _master_row()
        del row["breaks"]
        ayla(_day_payload(row))

        assert _get(client).json()["masters"][0]["breaks"]["state"] == "absent"

    def test_rows_no_field_pair_matches_are_named_unreadable(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(_day_payload(_master_row(breaks=[{"from_minute": 780, "to_minute": 840}])))

        body = _get(client).json()
        breaks = body["masters"][0]["breaks"]

        assert breaks["state"] == "unreadable"
        assert breaks["rows"] == []
        # Имена встреченных полей — наружу: следующий читатель узнает форму
        # из отчёта, а не из повторного выезда на пилот.
        assert breaks["seen_fields"] == ["from_minute", "to_minute"]
        # И экран обязан узнать об этом сводкой, а не обходом всех мастеров.
        assert body["unreadable_lists"] == ["breaks"]

    def test_known_field_names_with_unparsable_values_are_not_silently_dropped(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # Ловушка тоньше чужих имён: имена ТЕ, а значение мусор. Строка так
        # же непригодна, и обязана дать тот же явный отказ.
        ayla(_day_payload(_master_row(breaks=[{"start_local": "обед", "end_local": "14:00"}])))

        breaks = _get(client).json()["masters"][0]["breaks"]

        assert breaks["state"] == "unreadable"
        assert breaks["rows"] == []

    def test_absences_are_read_by_the_same_rule_as_breaks(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # Иначе получится осторожный ``breaks`` рядом с молчащим
        # ``absences`` — и щель просто переедет.
        ayla(_day_payload(_master_row(absences=[{"unknown": 1}])))

        body = _get(client).json()

        assert body["masters"][0]["absences"]["state"] == "unreadable"
        assert body["unreadable_lists"] == ["absences"]


class TestTheSourceRefusesByName:
    def test_an_unavailable_source_is_named_not_shown_as_an_empty_day(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(_day_payload(), exc=SalonUnavailable("upstream 502"))

        resp = _get(client)

        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"

    def test_a_payload_without_masters_refuses_instead_of_reading_as_nobody_works(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla({"date": "2026-09-10"})

        resp = _get(client)

        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"

    def test_the_local_source_is_named_rather_than_pretended(
        self, client: Client, owner_bot_user: BotUser, settings, monkeypatch
    ) -> None:
        # Флаг ВЫКЛЮЧЕН: салон правит локальные таблицы, и день Ayla их не
        # описывает. Показать его тут значило бы выдать чужую смену за свою.
        settings.BOOKING_VIA_AYLA_REST = False

        def _boom():  # pragma: no cover — вызов и есть то, чего быть не должно
            raise AssertionError("Ayla читается при выключенном флаге")

        monkeypatch.setattr("apps.admin_api.views_salon_frame.get_salon_client", _boom)

        resp = _get(client)

        assert resp.status_code == 503
        assert resp.json()["error"] == "frame_source_local_unsupported"

    def test_a_tenant_with_nobody_to_read_as_is_named(
        self, client: Client, receptionist_bot_user: BotUser, ayla
    ) -> None:
        # Ресепшн читать вправе, но Ayla проверяет ПРАВА названного человека,
        # а владельца/админа в теганте нет. Это факт настройки, и он обязан
        # назваться, а не превратиться в пустой день.
        ayla(_day_payload())

        resp = _get(client, user_id="5003")

        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_source_not_configured"

    def test_a_bad_date_is_refused_before_the_wire(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        fake = ayla(_day_payload())

        resp = _get(client, date="10.09.2026")

        assert resp.status_code == 400
        assert fake.calls == []


class TestWhoMayReadTheSalonsDay:
    def test_the_front_desk_may_read_it(
        self,
        client: Client,
        owner_bot_user: BotUser,
        receptionist_bot_user: BotUser,
        ayla,
    ) -> None:
        # §35 п. 1 (DRF-1552): ресепшн читает день салона. Кадр того же дня —
        # тот же контур; личный график мастера — другой вопрос (DRF-1640).
        ayla(_day_payload())

        assert _get(client, user_id="5003").status_code == 200

    def test_a_master_may_not(
        self, client: Client, owner_bot_user: BotUser, master_only_bot_user: BotUser, ayla
    ) -> None:
        ayla(_day_payload())

        assert _get(client, user_id=str(master_only_bot_user.channel_user_id)).status_code == 403
