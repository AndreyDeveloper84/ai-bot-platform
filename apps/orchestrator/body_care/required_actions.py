"""Реестр ``required_actions`` итога скрининга — контракт v0.2 §11, §16 (BOT-4b).

§11 называет поле ``required_actions[]`` без перечня. Кандидаты взяты из
роутинга §16 и BOT-2 (дизайн BOT-4, §4)::

    R0  CONTINUE_OR_MONITOR_PER_PROTOCOL
    R1  STOP_CURRENT_EXPOSURE, APPLY_PROTOCOL_AFTERCARE, BLOCK_NEXT_SPA_STEP
    R2  MEDICAL_ASSESSMENT_REQUIRED
    R3  S1_ROUTE
    —   REASK_REQUIRED (BOT-2, F-BC-002)

Как у симптомов BOT-7: реестр **закрыт** — политика не может вернуть
действие, которого здесь нет, — но версия :data:`REQUIRED_ACTION_REGISTRY_VERSION`
несёт пометку ``candidate``: утверждение словаря или его изменение — новая
версия, не тихая правка.

Какое действие при каком статусе — решает политика, не этот модуль. Здесь
нет таблицы «статус → действия»: она часть роутинга (D-5).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Final

#: Версия словаря: кандидаты §16 + BOT-2, не утверждены.
REQUIRED_ACTION_REGISTRY_VERSION: Final = "0.1.0-candidate"


class RequiredAction(StrEnum):
    """Действие, которого требует итог скрининга. Значение равно имени."""

    CONTINUE_OR_MONITOR_PER_PROTOCOL = "CONTINUE_OR_MONITOR_PER_PROTOCOL"
    STOP_CURRENT_EXPOSURE = "STOP_CURRENT_EXPOSURE"
    APPLY_PROTOCOL_AFTERCARE = "APPLY_PROTOCOL_AFTERCARE"
    BLOCK_NEXT_SPA_STEP = "BLOCK_NEXT_SPA_STEP"
    MEDICAL_ASSESSMENT_REQUIRED = "MEDICAL_ASSESSMENT_REQUIRED"
    S1_ROUTE = "S1_ROUTE"
    REASK_REQUIRED = "REASK_REQUIRED"


_ORDER = {action: i for i, action in enumerate(RequiredAction)}


class RequiredActionError(ValueError):
    """Действие вне реестра. Программная ошибка политики, не решение."""


def normalize_actions(actions: Iterable[Any]) -> tuple[RequiredAction, ...]:
    """Члены реестра без повторов, в порядке реестра — одинаковый итог, одинаковая запись."""

    if isinstance(actions, str):
        # Одна строка — не список: иначе "S1_ROUTE" разобралось бы по буквам.
        raise RequiredActionError("required_actions — последовательность членов реестра, не строка")
    seen: set[RequiredAction] = set()
    for item in actions:
        if not isinstance(item, RequiredAction):
            raise RequiredActionError(f"действие вне реестра: {item!r}")
        seen.add(item)
    return tuple(sorted(seen, key=_ORDER.__getitem__))


__all__ = [
    "REQUIRED_ACTION_REGISTRY_VERSION",
    "RequiredAction",
    "RequiredActionError",
    "normalize_actions",
]
