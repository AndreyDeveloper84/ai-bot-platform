"""Тексты отказа по бюджету распознавания в боте (DRF-2195, Сканер-1b).

Лестница отказов навыка сегодня двухступенчатая: «не разобралась» и «сервис
временно недоступен — попробуй через минуту». Штатный отказ по бюджету не
похож ни на то, ни на другое:

* через минуту НИЧЕГО не изменится — суточный потолок снимется в полночь;
* дорога есть и она рядом — записать еду словами; её и надо назвать.

Поэтому оба отказа получают собственный текст, и оба зовут писать словами.
Положительная пара — настоящая недоступность каталога — по-прежнему говорит
«попробуй через минуту»: иначе лист выродился бы в «у всех один текст».
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla.nutrition_client import NutritionUnavailableError
from apps.skills.food_scanner import skill as fs
from apps.skills.food_scanner.skill import FoodScannerSkill
from apps.skills.food_scanner.tests.test_skill import (  # переиспользуем стенд
    _context,
    _diary_consent_granted,  # noqa: F401 — autouse: согласие дневника выдано
    _enable_nutrition,  # noqa: F401 — autouse: ворота Вехи 1 открыты
    _personal_data_granted,  # noqa: F401 — autouse: PERSONAL_DATA выдано
)

pytestmark = pytest.mark.django_db


def _client_raising(exc: Exception) -> Mock:
    client = Mock()

    async def _scan(**kwargs):
        raise exc

    async def _log(**kwargs):
        raise exc

    client.scan_photo = _scan
    client.log_meal = _log
    return client


def _handle(ctx, client):
    with patch(
        "apps.skills.food_scanner.skill.get_nutrition_client",
        return_value=client,
    ):
        return FoodScannerSkill().handle(ctx)


class TestPhotoScanBudgetCopy:
    def test_daily_limit_says_today_and_offers_words(self) -> None:
        from apps.integrations.ayla.nutrition_client import ScanDailyLimitError

        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        result = _handle(ctx, _client_raising(ScanDailyLimitError("daily")))

        assert result.reply_text == fs.SCAN_DAILY_LIMIT_FALLBACK
        assert result.meta["reply_kind"] == "food_scanner_daily_limit"
        # Смысловые требования к тексту, а не только равенство константе:
        # человеку названы срок («сегодня») и дорога («словами»).
        assert "егодня" in result.reply_text
        assert "словам" in result.reply_text
        # И НЕ обещано «через минуту» — через минуту ничего не изменится.
        assert "через минуту" not in result.reply_text

    def test_budget_exhausted_offers_words(self) -> None:
        from apps.integrations.ayla.nutrition_client import ScanBudgetExhaustedError

        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        result = _handle(ctx, _client_raising(ScanBudgetExhaustedError("budget")))

        assert result.reply_text == fs.SCAN_BUDGET_EXHAUSTED_FALLBACK
        assert result.meta["reply_kind"] == "food_scanner_budget_exhausted"
        assert "словам" in result.reply_text
        assert "через минуту" not in result.reply_text

    def test_positive_pair_real_outage_still_says_a_minute(self) -> None:
        """Положительная пара: настоящая недоступность — прежний текст."""
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        result = _handle(ctx, _client_raising(NutritionUnavailableError("down")))

        assert result.reply_text == fs.AYLA_DOWN_FALLBACK
        assert "через минуту" in result.reply_text

    def test_budget_texts_are_distinct_from_each_other_and_from_outage(self) -> None:
        """Три разных отказа — три разных текста: иначе различать их незачем."""
        texts = {
            fs.SCAN_DAILY_LIMIT_FALLBACK,
            fs.SCAN_BUDGET_EXHAUSTED_FALLBACK,
            fs.AYLA_DOWN_FALLBACK,
            fs.NOT_RECOGNIZED_FALLBACK,
        }
        assert len(texts) == 4

    def test_no_internal_codes_leak(self) -> None:
        for text in (fs.SCAN_DAILY_LIMIT_FALLBACK, fs.SCAN_BUDGET_EXHAUSTED_FALLBACK):
            lowered = text.lower()
            assert "scan" not in lowered
            assert "limit" not in lowered
            assert "budget" not in lowered
            assert "429" not in lowered and "503" not in lowered
