"""Непродаваемое предложение: причина из ответа каталога и слова для людей (DRF-1989).

Каталог (DRF-1962) говорит «не продаётся» двумя способами: полем ребра
``catalog/specialist-services/`` — ``sellable`` / ``unsellable_reason`` — и
отказом создания записи ``422 SERVICE_NOT_ACTIVE`` с ``details.reason``. Здесь
один читатель на оба способа и одни слова на причину, чтобы чат, Mini App,
повтор визита и салонная админка не разошлись.

Тексты — рабочие версии на подтверждении владельца (W2): «у мастера не указана
цена» говорится только для ``price_below_minimum``, остальным причинам —
нейтральный текст без причины. Обещаний в текстах нет: никто не свяжется с
человеком сам, и текст этого не говорит.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Наружное имя отказа — одно для Mini App, чата и админки.
OFFER_NOT_SELLABLE_SLUG = "offer_not_sellable"

PRICE_BELOW_MINIMUM = "price_below_minimum"
INACTIVE = "inactive"
UNKNOWN = "unknown"

#: Причины, которые каталог называет (``services/offer_sellable.py``, DRF-1962);
#: всё остальное — ``unknown``, и предложение остаётся непродаваемым.
KNOWN_REASONS = frozenset({PRICE_BELOW_MINIMUM, INACTIVE})

#: Код отказа создания, в котором каталог несёт ``details.reason``.
SERVICE_NOT_ACTIVE = "SERVICE_NOT_ACTIVE"

_CLIENT_PRICE = (
    "Сейчас записаться на эту услугу онлайн нельзя: у мастера не указана цена. "
    "Напишите администратору салона."
)
_CLIENT_NEUTRAL = "Сейчас записаться на эту услугу онлайн нельзя. Напишите администратору салона."
_STAFF_PRICE = "Предложение не продаётся: цена ниже 1 ₽. Укажите цену услуги у мастера."
_STAFF_NEUTRAL = "Предложение сейчас не продаётся."


def _named(reason: object) -> str:
    return reason if isinstance(reason, str) and reason in KNOWN_REASONS else UNKNOWN


def reason_from_edge(row: Mapping[str, Any]) -> str | None:
    """Причина непродаваемого ребра; ``None`` — продаётся или ответ о продаже молчит.

    Ключа ``sellable`` нет → ``None``: каталог до DRF-1962 о продаже не говорит,
    и поведение остаётся прежним (порядок выкладки «бот раньше каталога»).
    Продаётся только явное ``true`` — то же правило, что у зеркала
    (``apps/catalog/services/http_client.py::_parse_sellable``).
    """
    if "sellable" not in row or row["sellable"] is True:
        return None
    return _named(row.get("unsellable_reason"))


def reason_from_refusal(code: str | None, details: Mapping[str, Any] | None) -> str | None:
    """Причина отказа создания записи; ``None`` — отказ не про продажу или безымянный.

    ``SERVICE_NOT_ACTIVE`` без ``details.reason`` — прежний безымянный отказ
    («услуга недоступна»), и его смысл здесь не меняется.
    """
    if code != SERVICE_NOT_ACTIVE or not isinstance(details, Mapping):
        return None
    reason = details.get("reason")
    if not reason:
        return None
    return _named(reason)


def client_text_for(reason: str | None) -> str:
    """Что читает клиент (чат, Mini App, повтор визита)."""
    return _CLIENT_PRICE if reason == PRICE_BELOW_MINIMUM else _CLIENT_NEUTRAL


def staff_text_for(reason: str | None) -> str:
    """Что читает администратор салона — с тем, что исправить, когда это известно."""
    return _STAFF_PRICE if reason == PRICE_BELOW_MINIMUM else _STAFF_NEUTRAL
