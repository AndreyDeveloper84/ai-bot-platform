"""DRF-1994 — тесты этого навыка проверяют контур питания ВКЛЮЧЁННЫМ.

До единого выключателя навык флаг ``NUTRITION_ENABLED`` не читал и тесты
работали при любом его значении. Теперь умолчание ``False`` (fail-closed
по решению владельца) даёт заглушку на каждом входе — и то, что пакет
всегда предполагал, названо явно. Выключенное поведение доказывается в
``apps/orchestrator/tests/test_nutrition_single_switch_1994.py``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _nutrition_contour_on(settings):
    settings.NUTRITION_ENABLED = True
