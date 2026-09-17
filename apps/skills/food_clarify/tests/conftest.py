"""DRF-2071 — тесты этого навыка проверяют контур питания ВКЛЮЧЁННЫМ.

До DRF-2071 ворота ``NUTRITION_ENABLED`` стояли только на оценке и записи
(``text_entry._gate``), и карточка «Это про еду?» с ветками без оценки
работала при любом значении флага. Теперь ворота в ``handle`` и умолчание
``False`` (fail-closed) даёт заглушку на каждом входе — и то, что пакет
всегда предполагал, названо явно. Тесты, которые проверяют выключенное
поведение, ставят ``False`` сами.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _nutrition_contour_on(settings):
    settings.NUTRITION_ENABLED = True
