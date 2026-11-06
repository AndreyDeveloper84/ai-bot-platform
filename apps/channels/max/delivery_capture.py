"""Доставка хода в Mini App вместо MAX — перехват исходящего на время хода (DRF-2799).

Решение владельца 06.10.2026: диалог продолжается там, где начат. Человек
пишет Ayla из Mini App — ответ приходит в Mini App, а не в чат бота.

Ход из Mini App — это ТОТ ЖЕ глобальный ход бота
(:func:`apps.channels.max.handler._handle_global_max_event_inner`), а не его
копия: гейт безопасности, согласия, передача оператору, команды памяти,
охрана исходящего и запись истории отрабатывают тем же кодом, в той же
``Conversation`` человека. Меняется одно — куда уходит ответ. Пока ручка
Mini App держит :func:`capturing`, три функции
:mod:`apps.channels.max.outbound`, через которые идёт ВЕСЬ исходящий поток
хода в MAX API (``send_message``, ``edit_message``, ``send_chat_action``),
не ходят в сеть, а складывают ответ сюда; ручка отдаёт его телом.

**Перехватывается только то, что адресовано этому человеку.** Отправка на
адрес диалога хода (``chat_id``) или на его MAX-id (``user_id``) — сюда;
любой другой адресат (оператор, служебный чат) получает своё как обычно:
уведомление оператору о реплике безопасности во время передачи — работа
бота, и Mini App её не отменяет. Правки сообщений (``edit_message``) во
время хода из Mini App относятся только к нему — у хода нет сообщений в
MAX, — поэтому перехватываются все. Индикаторы («печатает», «прочитано»)
для этого человека гасятся.

Состояние — ``ContextVar``: перехват виден только потоку ручки, которая его
поставила, и снимается при выходе из блока, в том числе по исключению.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InAppDelivery:
    """Куда ушёл бы ответ хода и что в итоге сказано человеку."""

    chat_id: str
    user_id: str
    replies: list[dict[str, Any]] = field(default_factory=list)

    def addressed_to_person(self, *, chat_id: str | None, user_id: str | None) -> bool:
        if user_id is not None:
            return str(user_id) == self.user_id
        return chat_id is not None and str(chat_id) == self.chat_id


_ACTIVE: ContextVar[InAppDelivery | None] = ContextVar("max_in_app_delivery", default=None)


def active() -> InAppDelivery | None:
    """Идёт ли сейчас ход из Mini App — и если да, его доставка."""
    return _ACTIVE.get()


@contextlib.contextmanager
def capturing(*, chat_id: str, user_id: str) -> Iterator[InAppDelivery]:
    """Перехватить исходящее этому человеку на время блока.

    Вложенный перехват запрещён: ход внутри хода — ошибка программиста, и
    тихо подменить чужую доставку было бы хуже громкого отказа.
    """
    if not user_id:
        raise ValueError("in-app delivery needs the person's MAX id")
    if _ACTIVE.get() is not None:
        raise RuntimeError("in-app delivery is already being captured on this context")
    delivery = InAppDelivery(chat_id=str(chat_id or ""), user_id=str(user_id))
    token = _ACTIVE.set(delivery)
    try:
        yield delivery
    finally:
        _ACTIVE.reset(token)
