"""Дверь поддержки в клиентском боте: «/start support» (DRF-2751).

Человек приходит по ссылке поддержки
(:func:`apps.channels.support_contact.client_support_link`) — MAX доставляет
стартовый параметр, парсер сворачивает его в текст «/start support». До этого
листа такой ход был обычным приветствием: намерение «мне нужна поддержка»
терялось на первом же шаге.

### Реплика, а не задача

На сам переход бот отвечает одной репликой с кнопкой и НЕ заводит задачу:
задача без единого слова клиента — шум для дежурного. Задача появляется,
когда человек сделал второй шаг:

* нажал «Позвать человека» (:data:`CB_SUPPORT_CALL`), или
* написал, что случилось, — следующим обычным сообщением.

«Следующее сообщение» помнится недолго (:data:`SUPPORT_ENTRY_TTL_SECONDS`) и
только до первого постороннего жеста: нажал другую кнопку или дал команду —
значит, ушёл по своим делам, и фраза «запиши на маникюр» через пять минут
не должна уехать в поддержку.

### Куда ложится задача

В очередь платформы — см. :func:`apps.orchestrator.handoff.route_support_request`.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.channels.support_contact import SUPPORT_START_PAYLOAD

logger = logging.getLogger(__name__)

#: Текст хода, которым приходит переход по ссылке поддержки.
SUPPORT_ENTRY_COMMAND = f"/start {SUPPORT_START_PAYLOAD}"

CB_SUPPORT_CALL = "cb:support:call"
SUPPORT_CALL_BUTTON = "Позвать человека"
SUPPORT_ENTRY_TEXT = (
    "Напиши, что случилось, — я передам человеку из поддержки Ayla. "
    "Или нажми кнопку, и я позову его сразу."
)

#: Сколько секунд после реплики следующее сообщение считается обращением.
SUPPORT_ENTRY_TTL_SECONDS = 15 * 60

TURN_ENTRY = "entry"
TURN_CALL = "call"
TURN_SAID = "said"


def _key(conversation: Any) -> str:
    return f"support_entry:{conversation.id}"


def remember_support_entry(conversation: Any) -> None:
    """Запомнить: человеку сказано «напиши, что случилось»."""

    from django.core.cache import cache

    try:
        cache.set(_key(conversation), 1, SUPPORT_ENTRY_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — нет кеша: остаётся кнопка, ход не падает
        logger.warning("support_entry.remember_failed conversation=%s", conversation.id)


def forget_support_entry(conversation: Any) -> None:
    from django.core.cache import cache

    try:
        cache.delete(_key(conversation))
    except Exception:  # noqa: BLE001
        logger.warning("support_entry.forget_failed conversation=%s", conversation.id)


def _awaited(conversation: Any) -> bool:
    from django.core.cache import cache

    try:
        return bool(cache.get(_key(conversation)))
    except Exception:  # noqa: BLE001 — нет кеша: «не ждём», фраза идёт обычным путём
        return False


def support_turn(text: str, conversation: Any) -> str | None:
    """Чем этот ход является для двери поддержки — или None.

    * :data:`TURN_ENTRY` — переход по ссылке поддержки;
    * :data:`TURN_CALL` — нажата «Позвать человека»;
    * :data:`TURN_SAID` — обычное сообщение сразу после реплики двери;
    * None — ход к поддержке не относится. Если при этом дверь ещё ждала, а
      человек нажал другую кнопку или дал команду, ожидание снимается.
    """

    stripped = (text or "").strip()
    if stripped == SUPPORT_ENTRY_COMMAND:
        return TURN_ENTRY
    if stripped == CB_SUPPORT_CALL:
        return TURN_CALL
    if not _awaited(conversation):
        return None
    if not stripped:
        # Пустой текст — вложение или голос; своя ветка, ожидание остаётся.
        return None
    if stripped.startswith(("cb:", "/")):
        forget_support_entry(conversation)
        return None
    return TURN_SAID


def support_entry_action_data() -> dict[str, Any]:
    return {
        "buttons": [{"label": SUPPORT_CALL_BUTTON, "callback": CB_SUPPORT_CALL}],
        "button_columns": 1,
    }
