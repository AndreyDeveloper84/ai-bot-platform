"""Ворота ЗАПИСИ в дневник питания — один предикат на всех писателей (DRF-2093).

До этого листа писателей дневника было четыре модуля, а ворот — три разных:
Mini App (вода, правка и возврат еды) спрашивал флаг и PERSONAL_DATA, чат
(вода, текст) — то же самое, и только сканер фото и текст F8 спрашивали ещё
и реестр ``food_diary_processing`` (:func:`apps.consent.nutrition.diary_is_granted`,
DRF-1963). Итог: согласие на дневник отозвано в Mini App — а стакан воды
из того же Mini App всё равно записан.

Здесь — единственный предикат :func:`diary_write_refusal`, и класс, на
который он обязан стоять, — **вызовы методов записи клиента питания**
(``log_meal`` / ``add_water`` / ``update_meal`` / ``restore_meal``), а не
список файлов: перепись по AST в
``apps/consent/tests/test_diary_write_gate_2093.py`` находит писателей по
вызовам и требует, чтобы каждый их модуль спрашивал этот предикат.
Удаление своей записи (``delete_meal``, ``undo_water``) согласия не требует —
убрать своё человек вправе всегда, это не новая обработка.

Три ворот, по порядку, каждое — своим именем:

1. ``nutrition_disabled`` — ``NUTRITION_ENABLED`` выключен (Mini App: 404,
   чат: текст «функция недоступна»);
2. ``consent_required`` — нет PERSONAL_DATA (Mini App: 403, чат: S2-отказ
   с кнопкой «Дать согласие», DRF-1968 — та кнопка выдаёт ИМЕННО
   PERSONAL_DATA);
3. ``food_diary_consent_required`` — нет действующего согласия дневника в
   реестре на текущий текст ``food-diary-v1`` (Mini App: 403 своим слагом,
   экран ведёт на согласие; чат: текст «открыть Mini App и подтвердить» —
   БЕЗ кнопки 1968: она выдала бы PERSONAL_DATA и вернула бы человека к тому
   же отказу; выдать согласие дневника из чата сегодня нечем).

Fail-closed: исключение реестра = согласие не доказано = записи нет.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

NUTRITION_DISABLED = "nutrition_disabled"
CONSENT_REQUIRED = "consent_required"
FOOD_DIARY_CONSENT_REQUIRED = "food_diary_consent_required"


def diary_write_refusal(bot_user: Any) -> str | None:
    """Почему запись в дневник ЭТОМУ человеку сейчас запрещена — или ``None``.

    Импорты ленивые и через модули: предикаты PERSONAL_DATA и реестра
    подменяются в тестах по своим адресам, и подмена обязана действовать
    на все писатели разом — иначе один из них тест проверял бы мимо.
    """
    from django.conf import settings

    if not getattr(settings, "NUTRITION_ENABLED", False):
        return NUTRITION_DISABLED

    from apps.orchestrator import personal_surface

    if not personal_surface.personal_records_consent_open(bot_user):
        return CONSENT_REQUIRED

    from apps.consent import nutrition

    try:
        diary_open = nutrition.diary_is_granted(bot_user)
    except Exception:  # noqa: BLE001 — fail-closed: согласие не доказано — записи нет
        logger.exception(
            "consent.diary_gate.registry_check_failed person=%s", getattr(bot_user, "pk", None)
        )
        diary_open = False
    if not diary_open:
        return FOOD_DIARY_CONSENT_REQUIRED
    return None


__all__ = [
    "CONSENT_REQUIRED",
    "FOOD_DIARY_CONSENT_REQUIRED",
    "NUTRITION_DISABLED",
    "diary_write_refusal",
]
