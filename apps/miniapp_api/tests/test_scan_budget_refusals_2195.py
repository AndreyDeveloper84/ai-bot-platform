"""Прокси Mini App называет отказы по бюджету их именем (DRF-2195, Сканер-1b).

``_food_text_catalog_refusal`` сегодня знает три исхода: «не распознала»
(400), «каталог лёг» (503) и «каталог отказал» (400 `ayla_bad_request`).
Штатный отказ по бюджету попадал бы в последний — безымянный — мешок, и
экран не смог бы сказать человеку ни «сегодня», ни «напиши словами»: слаг
``ayla_bad_request`` означает «мы послали каталогу чушь», то есть ошибку
программы, а не понятный человеку предел.

Каждый отказ — со своим слагом и своим кодом: 429 (личный потолок на
сутки) и 503 (общий дневной бюджет). Положительная пара — прежние три
исхода — остаётся на месте, иначе лист выродился бы в «всё одно и то же».
"""

from __future__ import annotations

import pytest

from apps.integrations.ayla.nutrition_client import (
    FoodNotRecognizedError,
    NutritionAPIError,
    NutritionUnavailableError,
)
from apps.miniapp_api.tests.test_food_scan_2098 import (  # переиспользуем стенд
    _patch_client,
    _scan,
    _settings,  # noqa: F401 — autouse: токен MAX, ворота дневника и фото
    bot_user,  # noqa: F401 — фикстура
    diary_consent,  # noqa: F401 — autouse
    personal_consent,  # noqa: F401 — autouse
)


@pytest.mark.django_db
class TestScanBudgetRefusalsHaveTheirOwnNames:
    def test_daily_limit_is_429_with_its_own_slug(self, client, bot_user) -> None:  # noqa: F811
        from apps.integrations.ayla.nutrition_client import ScanDailyLimitError

        patcher, _ = _patch_client(scan=ScanDailyLimitError("daily", retry_after=3600))
        with patcher:
            resp = _scan(client, bot_user)

        assert resp.status_code == 429
        assert resp.json()["error"] == "food_scan_daily_limit"

    def test_budget_exhausted_is_503_with_its_own_slug(self, client, bot_user) -> None:  # noqa: F811
        from apps.integrations.ayla.nutrition_client import ScanBudgetExhaustedError

        patcher, _ = _patch_client(scan=ScanBudgetExhaustedError("budget"))
        with patcher:
            resp = _scan(client, bot_user)

        assert resp.status_code == 503
        assert resp.json()["error"] == "food_scan_budget_exhausted"

    def test_budget_refusal_is_not_the_bad_request_bag(self, client, bot_user) -> None:  # noqa: F811
        """Штатный предел — не «мы послали каталогу чушь»."""
        from apps.integrations.ayla.nutrition_client import ScanDailyLimitError

        patcher, _ = _patch_client(scan=ScanDailyLimitError("daily"))
        with patcher:
            resp = _scan(client, bot_user)

        assert resp.json()["error"] != "ayla_bad_request"
        assert resp.json()["error"] != "nutrition_unavailable"

    def test_positive_pair_the_three_old_outcomes_are_untouched(
        self,
        client,
        bot_user,  # noqa: F811
    ) -> None:
        cases = [
            (FoodNotRecognizedError("nope"), 400, "food_not_recognized"),
            (NutritionUnavailableError("circuit_open"), 503, "nutrition_unavailable"),
            (NutritionAPIError("bad"), 400, "ayla_bad_request"),
        ]
        for exc, status, slug in cases:
            patcher, _ = _patch_client(scan=exc)
            with patcher:
                resp = _scan(client, bot_user)
            assert (resp.status_code, resp.json()["error"]) == (status, slug), exc
