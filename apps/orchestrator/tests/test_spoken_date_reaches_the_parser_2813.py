"""DRF-2813 — дата, сказанная голосом, доходит до разбора записи той же, что набранная.

``parse_explicit_date`` читает запись (``orchestrator.handoff``). Голосовое
приходит к нему через ``normalize_numbers``, и три формы до правки теряли
дату по дороге: год после даты («…две тысячи двадцать восьмого года» → запись
на текущий год), час перед датой («на десять пятого октября» → даты нет) и срок
в дательном («к пятому октября» → даты нет).

Ожидание — литерал при фиксированном «сегодня», а не «то же, что у набранного»:
два ``None`` тоже равны.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.orchestrator.time_preference import parse_explicit_date
from apps.speech.numbers import normalize_numbers

TODAY = date(2026, 10, 6)


@pytest.mark.parametrize(
    ("said", "typed", "expected"),
    [
        # Год за датой.
        (
            "пятнадцатого декабря две тысячи двадцать восьмого года",
            "15 декабря 2028 года",
            date(2028, 12, 15),
        ),
        # Прошедший год остаётся прошедшим — запись ответит «дата уже прошла»,
        # а не молча уедет на следующий.
        (
            "пятого мая две тысячи двадцать пятого года",
            "5 мая 2025 года",
            date(2025, 5, 5),
        ),
        # Час перед датой.
        (
            "запишите меня на десять двадцатого октября",
            "запишите меня на 10:00 20 октября",
            date(2026, 10, 20),
        ),
        ("в десять пятнадцатого октября", "в 10:00 15 октября", date(2026, 10, 15)),
        # Срок в дательном.
        ("к двадцать пятому октября", "к 25 октября", date(2026, 10, 25)),
        ("к пятому ноября", "к 5 ноября", date(2026, 11, 5)),
    ],
)
def test_the_spoken_date_is_the_date_the_typed_one_gives(
    said: str, typed: str, expected: date
) -> None:
    assert parse_explicit_date(typed, today=TODAY) == expected
    assert parse_explicit_date(normalize_numbers(said), today=TODAY) == expected


def test_an_hour_before_a_date_never_looks_like_a_clock_time() -> None:
    """«10 15 октября» в боте читается как время 10:15 — такого текста быть не должно."""
    from apps.skills.booking.lookup import _SELECTION_SPACED_TIME

    assert _SELECTION_SPACED_TIME.search("в 10 15 октября") is not None  # диалект есть
    heard = normalize_numbers("в десять пятнадцатого октября")
    assert heard == "в десять 15 октября"
    assert _SELECTION_SPACED_TIME.search(heard) is None
