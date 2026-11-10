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
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonUnavailable

from .conftest import init_data_header, make_master

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


# ─── DRF-2933: отсутствия, закрытия салона и мастер зеркала ──────────────────


def _absence(start_local: str, end_local: str, **extra) -> dict:
    """Отсутствие в форме каталога (``tenant_day_service.DayAbsence``).

    Моменты — полные ISO: ``start_at`` / ``end_at`` в UTC, ``*_local`` — в
    поясе мастера. Времени суток («ЧЧ:ММ») в строке нет.
    """

    return {
        "id": "off-1",
        "start_at": "2026-09-10T00:00:00+00:00",
        "end_at": "2026-09-10T01:00:00+00:00",
        "start_at_local": start_local,
        "end_at_local": end_local,
        "reason": "Больничный",
        **extra,
    }


class TestAbsencesComeAsMomentsNotHours:
    """Каталог шлёт отсутствие моментами, а не временем суток.

    Общий разбор интервала их не узнавал: любое непустое ``absences``
    отвечало ``unreadable``, и «Расписание» не могло нарисовать ни одного
    блока. Пилот этого не показал только потому, что отгулов в нём не было.
    """

    def test_an_absence_inside_the_day_reads_as_the_masters_hours(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(
            _day_payload(
                _master_row(
                    absences=[_absence("2026-09-10T14:00:00+03:00", "2026-09-10T16:00:00+03:00")]
                )
            )
        )

        body = _get(client, date="2026-09-10").json()

        assert body["masters"][0]["absences"]["state"] == "parsed"
        assert body["masters"][0]["absences"]["rows"] == [{"start": "14:00", "end": "16:00"}]
        assert body["unreadable_lists"] == []

    def test_the_offset_is_the_masters_and_is_not_converted(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # «10:00+10:00» — десять утра у мастера во Владивостоке. Перевод в
        # пояс сервера дал бы полночь.
        ayla(
            _day_payload(
                _master_row(
                    absences=[_absence("2026-09-10T10:00:00+10:00", "2026-09-10T12:30:00+10:00")]
                )
            )
        )

        rows = _get(client, date="2026-09-10").json()["masters"][0]["absences"]["rows"]

        assert rows == [{"start": "10:00", "end": "12:30"}]

    def test_a_vacation_over_the_day_is_cut_to_the_day_and_ends_at_24(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(
            _day_payload(
                _master_row(
                    absences=[_absence("2026-09-05T00:00:00+03:00", "2026-09-14T00:00:00+03:00")]
                )
            )
        )

        master = _get(client, date="2026-09-10").json()["masters"][0]

        assert master["absences"]["rows"] == [{"start": "00:00", "end": "24:00"}]
        # Флаг считает каталог по шаблону и изменению на дату; отгул его не
        # меняет, и вид не решает за каталог, что день нерабочий.
        assert master["is_working_day"] is True

    def test_the_day_of_the_answer_cuts_when_no_date_was_asked(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(
            _day_payload(
                _master_row(
                    absences=[_absence("2026-09-09T22:00:00+03:00", "2026-09-10T02:00:00+03:00")]
                ),
                date="2026-09-10",
            )
        )

        rows = _get(client).json()["masters"][0]["absences"]["rows"]

        assert rows == [{"start": "00:00", "end": "02:00"}]

    def test_an_absence_that_does_not_touch_the_day_is_no_absence_not_unreadable(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # Каталог режет отсутствия своим окном; строка, кончившаяся ровно в
        # полночь этого дня, опознана — просто не про сегодня.
        ayla(
            _day_payload(
                _master_row(
                    absences=[_absence("2026-09-09T18:00:00+03:00", "2026-09-10T00:00:00+03:00")]
                )
            )
        )

        body = _get(client, date="2026-09-10").json()

        assert body["masters"][0]["absences"]["state"] == "none"
        assert body["masters"][0]["absences"]["rows"] == []
        assert body["unreadable_lists"] == []

    def test_an_unreadable_row_is_not_hidden_by_a_neighbour_from_another_day(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # Одна строка опознана и не про эти сутки, другая не опознана вовсе.
        # «Отсутствий нет» здесь — неправда: одну строку мы не поняли.
        ayla(
            _day_payload(
                _master_row(
                    absences=[
                        _absence("2026-09-09T18:00:00+03:00", "2026-09-10T00:00:00+03:00"),
                        {"unknown": 1},
                    ]
                )
            )
        )

        body = _get(client, date="2026-09-10").json()

        assert body["masters"][0]["absences"]["state"] == "unreadable"
        assert body["unreadable_lists"] == ["absences"]

    def test_an_absence_shorter_than_a_minute_is_not_an_event(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # После обрезки до минут «18:00:10–18:00:40» и «…–00:00:30 этого дня»
        # стали бы событиями нулевой длины.
        ayla(
            _day_payload(
                _master_row(
                    absences=[
                        _absence("2026-09-10T18:00:10+03:00", "2026-09-10T18:00:40+03:00"),
                        _absence("2026-09-09T20:00:00+03:00", "2026-09-10T00:00:30+03:00"),
                        _absence("2026-09-10T14:00:00+03:00", "2026-09-10T16:00:00+03:00"),
                    ]
                )
            )
        )

        rows = _get(client, date="2026-09-10").json()["masters"][0]["absences"]["rows"]

        assert rows == [{"start": "14:00", "end": "16:00"}]

    def test_the_reason_stays_behind(self, client: Client, owner_bot_user: BotUser, ayla) -> None:
        # «Больничный» — сведения о человеке; кадр читает и ресепшн.
        ayla(
            _day_payload(
                _master_row(
                    absences=[_absence("2026-09-10T14:00:00+03:00", "2026-09-10T16:00:00+03:00")]
                )
            )
        )

        body = _get(client, date="2026-09-10").json()

        assert body["masters"][0]["absences"]["rows"], "отсутствие не разобрано — проверять нечего"
        assert "Больничный" not in resp_text(body)
        assert "reason" not in body["masters"][0]["absences"]["rows"][0]

    def test_moments_that_do_not_parse_are_unreadable(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(_day_payload(_master_row(absences=[_absence("вчера", "завтра")])))

        body = _get(client, date="2026-09-10").json()

        assert body["masters"][0]["absences"]["state"] == "unreadable"
        assert body["unreadable_lists"] == ["absences"]

    def test_the_hours_form_is_still_read(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(_day_payload(_master_row(absences=[{"start_local": "13:00", "end_local": "15:00"}])))

        rows = _get(client, date="2026-09-10").json()["masters"][0]["absences"]["rows"]

        assert rows == [{"start": "13:00", "end": "15:00"}]


class TestTheSalonsClosuresAreNotDropped:
    def test_a_full_day_and_a_partial_closure_come_through(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        payload = _day_payload()
        payload["closures"] = [
            {
                "id": "c-1",
                "start_local": None,
                "end_local": None,
                "is_full_day": True,
                "reason": "Санитарный день",
            },
            {
                "id": "c-2",
                "start_local": "10:00",
                "end_local": "14:00",
                "is_full_day": False,
                "reason": "",
            },
        ]
        ayla(payload)

        closures = _get(client).json()["closures"]

        assert closures["state"] == "parsed"
        assert closures["rows"] == [
            {"start": None, "end": None, "is_full_day": True, "reason": "Санитарный день"},
            {"start": "10:00", "end": "14:00", "is_full_day": False, "reason": ""},
        ]

    def test_no_closures_says_none_and_a_missing_key_says_absent(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        ayla(_day_payload())
        assert _get(client).json()["closures"]["state"] == "none"

        payload = _day_payload()
        del payload["closures"]
        ayla(payload)
        assert _get(client).json()["closures"]["state"] == "absent"

    def test_a_closure_with_a_garbled_bound_is_unreadable_not_a_full_day(
        self, client: Client, owner_bot_user: BotUser, ayla
    ) -> None:
        # Опечатка в одной границе не должна закрыть салон на весь день.
        payload = _day_payload()
        payload["closures"] = [{"id": "c-1", "start_local": "десять", "end_local": "14:00"}]
        ayla(payload)

        body = _get(client).json()

        assert body["closures"]["state"] == "unreadable"
        assert body["unreadable_lists"] == ["closures"]


class TestTheFrameNamesTheMirrorMaster:
    """``master_id`` — под каким id того же мастера знает день салона.

    ``specialist_id`` кадра — id каталога; день салона (``admin/day/``)
    отдаёт id строки зеркала. У строки синка они равны, у соло-мастера и
    склеенного приглашения — нет (DRF-1933), и без пары экран не может
    приложить смену к записям.
    """

    def test_a_synced_row_and_a_solo_row_both_resolve(
        self, client: Client, owner_bot_user: BotUser, tenant, ayla
    ) -> None:
        synced = make_master(tenant, name="Синк")
        solo = make_master(tenant, name="Соло")
        catalog_id = uuid.uuid4()
        CatalogMaster.all_tenants.filter(pk=solo.pk).update(catalog_specialist_id=catalog_id)
        ayla(
            _day_payload(
                _master_row(specialist_id=str(synced.pk), display_name="Синк"),
                _master_row(specialist_id=str(catalog_id), display_name="Соло"),
            )
        )

        masters = _get(client).json()["masters"]

        assert [m["master_id"] for m in masters] == [str(synced.pk), str(solo.pk)]
        # Id каталога остаётся как был: им пользуются те, кто говорит с каталогом.
        assert masters[1]["specialist_id"] == str(catalog_id)

    def test_a_row_the_mirror_does_not_know_is_null_not_guessed_by_name(
        self, client: Client, owner_bot_user: BotUser, tenant, ayla
    ) -> None:
        make_master(tenant, name="Ольга")
        ayla(
            _day_payload(
                _master_row(specialist_id=str(uuid.uuid4()), display_name="Ольга"),
                _master_row(specialist_id="sp-1", display_name="Не uuid"),
            )
        )

        masters = _get(client).json()["masters"]

        assert [m["display_name"] for m in masters] == ["Ольга", "Не uuid"]
        assert [m["master_id"] for m in masters] == [None, None]

    def test_an_archived_master_is_not_a_pair_the_day_does_not_show_him(
        self, client: Client, owner_bot_user: BotUser, tenant, ayla
    ) -> None:
        archived = make_master(tenant, name="Архив", archived_at=datetime.now(tz=dt_timezone.utc))
        ayla(_day_payload(_master_row(specialist_id=str(archived.pk), display_name="Архив")))

        masters = _get(client).json()["masters"]

        assert masters[0]["display_name"] == "Архив"
        assert masters[0]["master_id"] is None

    def test_another_salons_master_is_not_a_pair(
        self, client: Client, owner_bot_user: BotUser, other_tenant, ayla
    ) -> None:
        foreign = make_master(other_tenant, name="Чужой")
        ayla(_day_payload(_master_row(specialist_id=str(foreign.pk), display_name="Чужой")))

        masters = _get(client).json()["masters"]

        assert masters[0]["display_name"] == "Чужой"
        assert masters[0]["master_id"] is None
