"""Кнопки следующего шага на глобальном пути бота (DRF-2267, решение владельца CD §72).

Правило владельца: после каждого завершённого шага — 1–2 кнопки следующего
шага и «Меню»; тупиков, после которых человек «не знает, что дальше», нет.

Здесь — одно определение кнопок, которые вешаются под завершающие сообщения
разных модулей (консьерж, поиск, подмена стражем). Подпись и колбэк каждой —
те же, что у пунктов главного меню и экрана возврата: две подписи одной
кнопки читаются как две разные кнопки (§37 п.4), а колбэк, который в одном
месте отвечает, а в другом «не понял», хуже никакого (DRF-1492).

Импорты ленивые: модуль зовут из ``discovery`` и ``concierge``, а сами
подписи живут в ``discovery`` и в меню витрины.
"""

from __future__ import annotations

from typing import Any

#: «Меню» — подпись экрана возврата (``global_onboarding.RETURNING_LABEL_MENU``).
MENU_LABEL = "Меню"
#: «Подобрать услугу» — подпись пункта меню витрины и экрана возврата.
DISCOVER_LABEL = "Подобрать услугу"
#: «Записать еду» — подпись экрана возврата (``RETURNING_LABEL_LOG_FOOD``).
LOG_FOOD_LABEL = "Записать еду"


def discover_button() -> dict[str, str]:
    """«Подобрать услугу» — та же фраза, что у пункта главного меню."""
    from apps.skills.menu.marketplace import DISCOVER_TAP_TEXT

    return {"label": DISCOVER_LABEL, "callback": DISCOVER_TAP_TEXT}


def salons_button() -> dict[str, str]:
    """«Найти салон» — ``cb:catalog:salons`` (одно определение, ``discovery``)."""
    from apps.orchestrator.discovery import show_salons_button

    return show_salons_button()


def menu_button() -> dict[str, str]:
    """«Меню» — ``cb:menu:help`` → «Что ты умеешь?» → меню из восьми (DRF-1491)."""
    from apps.skills.menu.matching import CALLBACK_MENU_HELP

    return {"label": MENU_LABEL, "callback": CALLBACK_MENU_HELP}


def log_food_button() -> dict[str, str]:
    """«Записать еду» — ``cb:welcome:food``, как на экране возврата.

    Ответ на него — приглашение прислать фото блюда или название, с воротами
    дневника (``global_onboarding._food_prompt_reply``): без согласия человек
    получает объяснение и путь к согласию, а не приглашение, которому
    откажут на следующем ходу.
    """
    from apps.channels.max.global_onboarding import CALLBACK_LOG_FOOD

    return {"label": LOG_FOOD_LABEL, "callback": CALLBACK_LOG_FOOD}


def diary_button() -> dict[str, str]:
    """«Мой дневник» — ``CHIP_DIARY`` (одно определение, ``personal_surface``)."""
    from apps.orchestrator.personal_surface import CHIP_DIARY

    return dict(CHIP_DIARY)


def water_button() -> dict[str, str]:
    """«Записать стакан воды» — ``CHIP_WATER`` (одно определение, ``personal_surface``)."""
    from apps.orchestrator.personal_surface import CHIP_WATER

    return dict(CHIP_WATER)


def after_entry_buttons() -> list[dict[str, str]]:
    """Под записанным в дневник (еда, вода): «Мой дневник» и «Меню».

    «Мой дневник» — только там, где тап дойдёт до дневника
    (:func:`personal_surface.diary_is_reachable`, глобальный путь): на
    лестнице тенанта ветки для него нет, и кнопка ответила бы «не понял»
    (DRF-1492). «Меню» отвечает на обоих путях.
    """
    from apps.orchestrator import personal_surface

    buttons = [diary_button()] if personal_surface.diary_is_reachable() else []
    return [*buttons, menu_button()]


def next_step_action_data(*buttons: dict[str, str]) -> dict[str, Any]:
    """``action_data`` с кнопками следующего шага — столбиком, как у остального пути."""
    return {"buttons": list(buttons), "button_columns": 1}


__all__ = [
    "DISCOVER_LABEL",
    "LOG_FOOD_LABEL",
    "MENU_LABEL",
    "after_entry_buttons",
    "diary_button",
    "discover_button",
    "log_food_button",
    "menu_button",
    "water_button",
    "next_step_action_data",
    "salons_button",
]
