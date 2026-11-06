"""DRF-2981 — словарь команд: что не бывает ответом на вопрос памяти и продолжением записи.

Чистый модуль ``apps.orchestrator.turn_commands``, без БД.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.turn_commands import (
    mentions_booking,
    names_a_booking_stop,
    names_a_command,
)
from apps.skills.booking.lookup import booking_mutation_flow

COMMANDS = [
    # таблица замера
    "отмени запись на завтра",
    "удали запись на завтра",
    "удали, пожалуйста, запись на завтра",
    "забудь про запись",
    "сотри бронь на субботу",
    "перенеси запись на пятницу вечером",
    "отмени запись на вечер",
    "отменить запись в центре",
    "забудь что я веган",
    "удали мою запись к мастеру Ольге за 2000",
    "запиши меня на вечер",
    # формы, найденные ревью плана
    "отменяй запись",
    "давай отменим запись на завтра",
    "Отмените-ка запись",
    "хочу отменить запись",
    "убери запись на завтра",
    "сними запись",
    "снимите меня с записи",
    "откажусь от записи",
    "давай перенесём на пятницу",
    "сдвинь запись на вечер",
    "запиши на субботу вечер",
    "запиши к Ольге на вечер",
    "забронируй на субботу вечер",
    "найди массаж на вечер",
]

ANSWERS = [
    # отрицание — не команда: «не забудь» команда памяти отдаёт дальше (DRF-2887)
    "не забудь, я веган",
    "не забудь: живу в Заречном",
    "не отменяй",
    # корни в чужих словах
    "работаю удалённо",
    "удалённо работаю, офис в центре",
    "отменный район, Центральный",
    "удаление волос",
    # «запишите:» — запиши, что я скажу, а не «запиши меня»
    "запишите: Ленинский",
    "Субботинский район",
    "в Ленинском районе",
    "вечером",
    "до 2000",
]


@pytest.mark.parametrize("text", COMMANDS)
def test_a_command_is_named(text: str) -> None:
    assert names_a_command(text)


@pytest.mark.parametrize("text", ANSWERS)
def test_an_answer_is_not_a_command(text: str) -> None:
    # Близнецы-команды — COMMANDS выше: тот же предикат их находит.
    assert not names_a_command(text)  # empty-assert-ok: близнецы — COMMANDS


BOOKING_STOPS = [
    "отмени запись на завтра",
    "удали запись на завтра",
    "отменяй запись на завтра",
    "сотри бронь на субботу",
    "забудь про запись на завтра",
    "сними запись",
    "убери запись на завтра",
    "откажусь от записи",
]

#: Посреди записи — исправление дня, а не стоп: глагол без записи.
NOT_BOOKING_STOPS = [
    "отмени, давай на завтра",
    "нет, отмени, лучше на послезавтра",
    "удали это, давай в пятницу вечером",
    "сотри, на субботу",
    "перенеси на завтра",
    "не отменяй, давай на завтра",
    "не отменяй запись",
]


@pytest.mark.parametrize("text", BOOKING_STOPS)
def test_a_booking_stop_is_named(text: str) -> None:
    assert names_a_booking_stop(text)


@pytest.mark.parametrize("text", NOT_BOOKING_STOPS)
def test_a_day_correction_is_not_a_booking_stop(text: str) -> None:
    # Близнецы — BOOKING_STOPS выше: тот же предикат их находит.
    assert not names_a_booking_stop(text)  # empty-assert-ok: близнецы — BOOKING_STOPS


@pytest.mark.parametrize(
    "text",
    [t for t in BOOKING_STOPS if t.startswith(("отмен", "удал")) or t.startswith("откаж")],
)
def test_the_booking_skill_agrees_these_are_cancellations(text: str) -> None:
    """Два словаря отмены — здесь и в ``booking.lookup`` — не расходятся."""
    assert names_a_booking_stop(text)
    assert booking_mutation_flow(text) == "cancel"


def test_the_booking_noun_is_closed_form() -> None:
    assert mentions_booking("отмени бронь")
    assert mentions_booking("мои записи")
    assert not mentions_booking("записаться на маникюр")
    assert not mentions_booking("записка от мастера")
