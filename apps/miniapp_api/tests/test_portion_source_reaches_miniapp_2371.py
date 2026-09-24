"""Признак происхождения порции доезжает до Mini App (DRF-2371).

Экран решает, называть число или спрашивать вес, по `portion_source`.
Признак живёт ВНУТРИ `nutrition` — так его кладёт каталог
(`FoodScanResponseSerializer`), и ответ Mini App API собирается белым
списком ключей. Стоит кому-то «почистить» этот список или переложить
признак на верхний уровень — и весь разбор на экране станет мёртвым
кодом: поле не придёт, отсутствие прочитается как «не названо», а число
исчезнет для всех. Ровно так этот разбор и был написан в первой версии,
пока ревью не назвало это.

Узел закрывает путь целиком: от ответа каталога до тела ответа Mini App.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.miniapp_api.tests.test_food_scan_2098 import (  # переиспользуем стенд
    PHOTO,
    _Scan,
    _patch_client,
    _scan,
    bot_user,  # noqa: F401 — фикстура
    diary_consent,  # noqa: F401 — autouse
    personal_consent,  # noqa: F401 — autouse
    _settings,  # noqa: F401 — autouse
)


pytestmark = pytest.mark.django_db


def _scan_with(portion_source: str | None) -> _Scan:
    nutrition: dict[str, Any] = {
        "calories": 250,
        "protein_g": 12,
        "fat_g": 8,
        "carbs_g": 32,
    }
    if portion_source is not None:
        nutrition["portion_source"] = portion_source
    return _Scan(nutrition=nutrition)


class TestTheProvenanceSurvivesTheWhitelist:
    @pytest.mark.parametrize("wire", ["provider", "typical", "unknown"])
    def test_the_answer_carries_it_verbatim(self, client, bot_user, wire) -> None:  # noqa: F811
        patcher, _ = _patch_client(scan=_scan_with(wire))
        with patcher:
            resp = _scan(client, bot_user, image=PHOTO)

        assert resp.status_code == 200
        body = resp.json()
        # Сначала о наличии: ответ собран и несёт числа.
        assert body["nutrition"]["calories"] == 250
        # И признак — ровно тем же значением, без перевода по дороге.
        assert body["nutrition"]["portion_source"] == wire

    def test_an_old_answer_without_the_field_stays_without_it(
        self,
        client,
        bot_user,  # noqa: F811
    ) -> None:
        patcher, _ = _patch_client(scan=_scan_with(None))
        with patcher:
            resp = _scan(client, bot_user, image=PHOTO)

        assert resp.status_code == 200
        nutrition = resp.json()["nutrition"]
        # Сначала о наличии: ответ собран и числа на месте.
        assert nutrition["calories"] == 250
        # Отсутствие поля экран читает как «вес не назван» — подставлять
        # сюда значение по умолчанию значило бы решать за каталог.
        assert "portion_source" not in nutrition
