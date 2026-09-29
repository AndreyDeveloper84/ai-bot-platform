"""Фразы показа покрывают весь словарь типов питания (DRF-2392).

Фразы — не значения, но ПО значениям: пропусти одно, и строка о человеке
молча не покажется. Узел стоит здесь, рядом с тем, что он держит, а не в
тестах оркестратора: тянуть закрытое имя чужого приложения через границу —
то же расхождение, только в другую сторону.

Сами слова здесь не проверяются: их утверждает владелец.
"""

from __future__ import annotations

from apps.integrations.ayla.diet_types import CATALOG_DIET_TYPES
from apps.persona.memory_surface import _DECLARED_DIET_PHRASES


def test_every_value_has_a_phrase() -> None:
    assert set(_DECLARED_DIET_PHRASES) == set(CATALOG_DIET_TYPES)
