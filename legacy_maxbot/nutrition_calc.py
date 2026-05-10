"""Pure-math утилиты для анкеты (Phase 3.1 Part 1).

Вся остальная nutrition-математика (BMR Mifflin-St Jeor / норма ккал /
БЖУ / water_ml) живёт на Ayla side — см. `docs/plans/maxbot-phase3-ayla-spec.md`
§1.2 server-side обязанности. Здесь только локальный BMI для ladder
(нужен ДО отправки в Ayla, чтобы показать предупреждение).
"""
from __future__ import annotations


def calc_bmi(*, weight_kg: float, height_cm: float) -> float:
    """Body Mass Index = weight (kg) / height (m)^2.

    Args:
        weight_kg: Вес в килограммах.
        height_cm: Рост в сантиметрах.

    Returns:
        BMI как float.

    Raises:
        ValueError: если height_cm <= 0 (защита от деления на ноль —
            возможно при invalid FSM state, когда юзер перешёл на goal
            без weight/height).
    """
    if height_cm <= 0:
        raise ValueError("height_cm must be positive")
    height_m = height_cm / 100.0
    return weight_kg / (height_m * height_m)
