"""DRF-2071 — тесты этого навыка проверяют контур питания ВКЛЮЧЁННЫМ.

До DRF-2071 навык правки (``cb:food:correct:*`` и ответ на вопрос) флаг
``NUTRITION_ENABLED`` не читал вовсе — единственный вход контура питания,
не закрытый выключателем DRF-1994. Теперь умолчание ``False`` (fail-closed)
даёт заглушку на каждом входе — и то, что пакет всегда предполагал, названо
явно. Выключенное поведение доказывается в ``test_nutrition_off_2071``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _nutrition_contour_on(settings):
    settings.NUTRITION_ENABLED = True
