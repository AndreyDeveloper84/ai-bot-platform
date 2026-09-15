"""Непродаваемое предложение: причина из ответа каталога и слова для людей (DRF-1989).

Каталог (#477, DRF-1962) говорит «не продаётся» двумя способами:

* ребро ``catalog/specialist-services/`` несёт ``sellable`` / ``unsellable_reason``;
* создание записи отвечает ``422 SERVICE_NOT_ACTIVE`` с ``details.reason``.

Здесь закрепляется один читатель на оба способа и одни слова на причину:

* ключа ``sellable`` нет → ``None``: каталог до #477 говорит о продаже молча,
  поведение прежнее;
* причина из закрытого словаря, всё остальное — ``unknown``;
* ``SERVICE_NOT_ACTIVE`` без ``details.reason`` → ``None`` (прежний безымянный
  отказ, его смысл не меняется);
* «у мастера не указана цена» — только для ``price_below_minimum``; ``inactive``
  и ``unknown`` получают нейтральный текст без причины; обещаний в текстах нет.

Тексты — рабочие версии, на подтверждение владельцу (W2). Константы ниже —
единственное место, где тесты других поверхностей берут ожидаемые слова.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

import pytest

MODULE = "apps.integrations.ayla.offer_refusal"

CLIENT_PRICE = (
    "Сейчас записаться на эту услугу онлайн нельзя: у мастера не указана цена. "
    "Напишите администратору салона."
)
CLIENT_NEUTRAL = "Сейчас записаться на эту услугу онлайн нельзя. Напишите администратору салона."
STAFF_PRICE = "Предложение не продаётся: цена ниже 1 ₽. Укажите цену услуги у мастера."
STAFF_NEUTRAL = "Предложение сейчас не продаётся."

#: Слова, которые обещают действие, которого никто не совершит.
PROMISES = ("свяж", "перезвон", "сообщим", "скоро", "уведомим")


def _fn(name: str) -> Callable[..., Any]:
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError:
        module = None
    fn = getattr(module, name, None)
    assert fn is not None, f"нет {MODULE}.{name}"
    return fn


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"sellable": False, "unsellable_reason": "price_below_minimum"}, "price_below_minimum"),
        ({"sellable": False, "unsellable_reason": "inactive"}, "inactive"),
        ({"sellable": False, "unsellable_reason": "renamed_reason"}, "unknown"),
        ({"sellable": False}, "unknown"),
        ({"sellable": True, "unsellable_reason": None}, None),
        ({"price": "1500.00", "duration_minutes": 60}, None),
    ],
    ids=["price_below_minimum", "inactive", "unnamed", "no_reason", "sellable", "no_key"],
)
def test_reason_from_edge(row: dict[str, Any], expected: str | None) -> None:
    assert _fn("reason_from_edge")(row) == expected


@pytest.mark.parametrize(
    ("code", "details", "expected"),
    [
        ("SERVICE_NOT_ACTIVE", {"reason": "price_below_minimum"}, "price_below_minimum"),
        ("SERVICE_NOT_ACTIVE", {"reason": "inactive"}, "inactive"),
        ("SERVICE_NOT_ACTIVE", {"reason": "renamed_reason"}, "unknown"),
        ("SERVICE_NOT_ACTIVE", None, None),
        ("SLOT_UNAVAILABLE", {"reason": "price_below_minimum"}, None),
    ],
    ids=["price_below_minimum", "inactive", "unnamed", "no_details", "other_code"],
)
def test_reason_from_refusal(
    code: str, details: dict[str, Any] | None, expected: str | None
) -> None:
    assert _fn("reason_from_refusal")(code, details) == expected


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("price_below_minimum", CLIENT_PRICE),
        ("inactive", CLIENT_NEUTRAL),
        ("unknown", CLIENT_NEUTRAL),
    ],
)
def test_client_text_names_the_price_only_when_it_is_the_reason(reason: str, expected: str) -> None:
    text = _fn("client_text_for")(reason)

    assert text == expected
    assert not any(word in text.lower() for word in PROMISES), text


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("price_below_minimum", STAFF_PRICE),
        ("inactive", STAFF_NEUTRAL),
        ("unknown", STAFF_NEUTRAL),
    ],
)
def test_staff_text_names_the_price_only_when_it_is_the_reason(reason: str, expected: str) -> None:
    assert _fn("staff_text_for")(reason) == expected
