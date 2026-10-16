"""DRF-2891 — «удали запись на завтра» не команда памяти и не стирает память.

Замер на dev ``d52b1c37`` (``handle_memory_command``, у человека одна строка):

* реплики задачи — «удали запись на завтра», «удали, пожалуйста, запись на
  завтра», «забудь про запись» — получали «Не совсем поняла, что именно
  забыть…», при пустой памяти «Мне пока нечего о тебе забывать»: ход до
  обычного разбора не доходил;
* хуже: подстрочные стебли доменов ловили слова про визит — «удали запись к
  мастеру» СТИРАЛА любимых мастеров, «…на вечер» и «…на выходные» — «когда
  приходишь», «…в другом районе» — районы, «…за 2000 рублей» — бюджет;
* «удали все записи», «сотри все брони» отвечали запросом «забудь всё»: ответ
  «удалить» стёр бы всю память.

Узлы:

* p1 — реплики задачи отпускают ход, при памяти и без неё;
* p2 — фраза про запись не стирает домен ни в голове предмета, ни в середине;
* p3 — «все записи» / «все брони» — не «забудь всё»;
* p4 — «записи обо мне», «запись что я веган» — по-прежнему команда памяти.
  Иначе ход ушёл бы дальше, а обработчик после хода, не бывшего командой,
  запускает экстрактор фактов — и «сотри запись что я веган» записала бы
  «веган» заново;
* p5 — близнец: настоящие команды и кнопки «Забыть: …» работают как прежде;
* p6 — «удали запись в дневнике питания» (бот сам так советует) не трогает
  ни питание, ни блюда;
* p7 — «сотри мой номер» — сознательное отступление от узла задачи: телефон —
  персональные данные, отпущенная реплика ушла бы модели без стирания;
* p8 — регулярки линейны на длинном входе.
"""

from __future__ import annotations

import time
import uuid

import pytest

from apps.consent.services import record_global_consent
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_reader import read_green_entries
from apps.persona import memory_commands
from apps.persona.memory_commands import (
    FORGET_ALL_PROMPT,
    MemoryCommandResult,
    handle_memory_command,
    memory_show_chips,
)

pytestmark = pytest.mark.django_db(transaction=True)

VEGAN = [("diet", "vegan")]


@pytest.fixture
def make_person(settings: object, monkeypatch: pytest.MonkeyPatch):
    """Человек с заданными строками памяти. Анкета Ayla не предмет — мост заглушён."""
    settings.STRICT_TENANT_SCOPE = "strict"  # type: ignore[attr-defined]
    monkeypatch.setattr(memory_commands, "_bridge_clear", lambda *args, **kwargs: None)
    monkeypatch.setattr(memory_commands, "_declared_phrases_for_show", lambda *args: {})

    def make(rows: list[tuple[str, str]]) -> BotUser:
        user_id = uuid.uuid4()
        bot_user = resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id=f"2891-{user_id.hex[:10]}",
            ayla_user_id=user_id,
        )
        record_global_consent(bot_user, source="welcome")
        context = UserPersonalContext.objects.create(user_id=user_id)
        for key, value in rows:
            MemoryEntry.objects.create(
                user_id=user_id,
                personal_context=context,
                sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
                source=MemoryEntry.SOURCE_EXPLICIT,
                provenance=MemoryEntry.PROVENANCE_USER_STATED,
                kind="lifestyle",
                content={"key": key, "value": value},
            )
        return bot_user

    return make


def _uid(person: BotUser) -> uuid.UUID:
    assert person.ayla_user_id is not None
    return person.ayla_user_id


def _say(person: BotUser, text: str) -> MemoryCommandResult | None:
    return handle_memory_command(user_id=_uid(person), text=text, bot_user=person)


def _rows(person: BotUser) -> int:
    return len(read_green_entries(_uid(person)))


TICKET = [
    "удали запись на завтра",
    "удали, пожалуйста, запись на завтра",
    "забудь про запись",
    "сотри бронь на завтра",
    "удали мою запись",
    "удали завтрашнюю запись",
    "Удали запись на завтра, пожалуйста.",
]


@pytest.mark.parametrize("rows", [VEGAN, []], ids=["memory", "empty"])
@pytest.mark.parametrize("text", TICKET)
def test_p1_a_booking_phrase_is_not_a_memory_command(make_person, rows, text) -> None:
    person = make_person(rows)

    result = _say(person, text)

    assert result is None
    assert _rows(person) == len(rows)


#: (что в памяти, реплика). Первые шесть стирали домен на ``d52b1c37``; дальше —
#: формы, где существительное не в голове предмета и ловит его второй
#: предохранитель (``_FOREIGN_NOUN_ANYWHERE_RE``).
BOOKING_OVER_A_DOMAIN = [
    (("favorite_masters", "Ольга"), "удали запись к мастеру"),
    (("favorite_masters", "Ольга"), "удали мою запись к мастеру Ольге"),
    (("visit_context", "evening"), "удали запись на вечер"),
    (("visit_context", "weekend"), "удали запись на выходные"),
    (("preferred_districts", "центр"), "удали запись в другом районе"),
    (("price_range", "до 2000"), "удали запись за 2000 рублей"),
    (("favorite_masters", "Ольга"), "удали мне запись к мастеру"),
    (("visit_context", "evening"), "удали у меня запись на вечер"),
    (("favorite_masters", "Ольга"), "удали вчерашнюю запись к мастеру"),
    (("visit_context", "evening"), "удали на вечер запись"),
    (("favorite_masters", "Ольга"), "удали субботнюю запись к мастеру"),
    (("favorite_masters", "Ольга"), "сотри к мастеру бронь"),
]


@pytest.mark.parametrize(("row", "text"), BOOKING_OVER_A_DOMAIN)
def test_p2_a_booking_phrase_never_erases_a_domain(make_person, row, text) -> None:
    person = make_person([row])

    result = _say(person, text)

    assert result is None or not result.erased
    assert _rows(person) == 1


@pytest.mark.parametrize(
    "text",
    [
        "удали все записи",
        "удали все мои записи",
        "сотри все брони",
        "забудь все записи",
    ],
)
def test_p3_all_bookings_is_not_forget_all(make_person, text) -> None:
    person = make_person(VEGAN)

    result = _say(person, text)

    assert result is None
    assert _rows(person) == 1


@pytest.mark.parametrize(
    "text",
    [
        "удали все записи обо мне",
        "сотри все записи про меня",
        "забудь все записи обо мне",
        "удали все свои записи",
        "удали все записи обо мне пожалуйста",
    ],
)
def test_p4_a_records_about_me_is_still_forget_all(make_person, text) -> None:
    person = make_person(VEGAN)

    result = _say(person, text)

    assert result is not None
    assert result.text == FORGET_ALL_PROMPT
    assert _rows(person) == 1


@pytest.mark.parametrize("text", ["сотри запись что я веган", "удали запись о том что я веган"])
def test_p4_b_a_record_naming_the_fact_still_erases_it(make_person, text) -> None:
    person = make_person(VEGAN)

    result = _say(person, text)

    assert result is not None
    assert result.erased
    assert _rows(person) == 0  # empty-assert-ok: строка была — make_person(VEGAN)


@pytest.mark.parametrize("text", ["удали мои записи о питании", "удали записи о моём питании"])
def test_p4_c_records_about_a_domain_still_erase_the_domain(make_person, text) -> None:
    person = make_person(VEGAN)

    result = _say(person, text)

    assert result is not None
    assert result.erased
    assert _rows(person) == 0  # empty-assert-ok: строка была — make_person(VEGAN)


def test_p4_d_records_about_me_with_empty_memory_still_answers(make_person) -> None:
    person = make_person([])

    result = _say(person, "удали записи обо мне")

    assert result is not None
    assert result.text == "Мне пока нечего о тебе забывать."


@pytest.mark.parametrize(
    ("row", "text"),
    [
        (("diet", "vegan"), "забудь, что я веган"),
        (("diet", "vegan"), "забудь про питание"),
        (("diet", "vegan"), "забудь всё про моё питание"),
        (("favorite_masters", "Ольга"), "забудь про мастера"),
        (("preferred_districts", "центр"), "забудь про районы"),
        (("visit_context", "evening"), "забудь про мои визиты по выходным"),
    ],
)
def test_p5_a_twin_real_commands_still_erase(make_person, row, text) -> None:
    person = make_person([row])

    result = _say(person, text)

    assert result is not None
    assert result.erased
    assert _rows(person) == 0  # empty-assert-ok: строка была — make_person([row])


@pytest.mark.parametrize("text", ["забудь всё", "удали всё", "сотри всё", "сотри память"])
def test_p5_b_twin_forget_all_still_asks(make_person, text) -> None:
    person = make_person(VEGAN)

    result = _say(person, text)

    assert result is not None
    assert result.text == FORGET_ALL_PROMPT


DOMAIN_ROWS = [
    ("diet", "vegan"),
    ("preferred_time_slots", "evening"),
    ("preferred_districts", "центр"),
    ("price_range", "до 2000"),
    ("favorite_masters", "Ольга"),
    ("city", "Пенза"),
    pytest.param(
        ("visit_context", "evening"),
        # Найдено этим тестом, на dev ``d52b1c37`` так же: кнопка шлёт «забудь
        # когда приходишь», а стебель домена — «когда прихож» (через «ж»), и
        # «приходишь» в него не попадает. Ответ — уточнение, строка цела.
        # DRF-2979; strict — узел покраснеет, когда её починят.
        marks=pytest.mark.xfail(
            strict=True, reason="DRF-2979: стебель «когда прихож» не ловит кнопку"
        ),
        id="visit_context",
    ),
]


@pytest.mark.parametrize("row", DOMAIN_ROWS, ids=lambda row: row[0])
def test_p5_c_twin_every_forget_chip_still_erases_its_domain(make_person, row) -> None:
    person = make_person([row])
    chips = memory_show_chips(person, user_id=_uid(person))
    assert len(chips) == 1, chips

    result = _say(person, chips[0]["callback"])

    assert result is not None
    assert result.erased
    assert _rows(person) == 0  # empty-assert-ok: строка была — make_person([row])


def test_p6_the_diary_advice_does_not_touch_food_memory(make_person) -> None:
    person = make_person([("diet", "vegan"), ("food_dish_name:борщ", "Свекольник")])

    result = _say(person, "удали запись в дневнике питания")

    assert result is None
    assert _rows(person) == 2


@pytest.mark.parametrize("text", ["сотри мой номер", "забудь мой номер телефона"])
def test_p7_a_phone_number_stays_a_clarification(make_person, text) -> None:
    person = make_person(VEGAN)

    result = _say(person, text)

    assert result is not None
    assert result.text.startswith("Не совсем поняла, что именно забыть.")
    assert _rows(person) == 1


@pytest.mark.parametrize(
    "regex",
    [
        memory_commands._FOREIGN_OBJECT_RE.match,
        memory_commands._FOREIGN_NOUN_ANYWHERE_RE.search,
        memory_commands._FORGET_ALL_RE.search,
    ],
)
def test_p8_the_new_patterns_stay_linear(regex) -> None:
    hostile = [
        "мои " * 10_000 + "x",
        "все " * 10_000 + "записи о",
        "запись " * 5_000,
        "удали все " * 3_000,
    ]

    started = time.perf_counter()
    for text in hostile:
        regex(text)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5, elapsed
