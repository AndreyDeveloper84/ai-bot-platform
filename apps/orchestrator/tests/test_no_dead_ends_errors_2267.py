"""DRF-2267 срез 3 — ответы-ошибки дневника и воды без тупиков.

Правило владельца CD §72: после завершённого шага — 1–2 кнопки следующего
шага и «Меню». Отказ — тоже завершённый шаг: человек, которому не записали
еду, остаётся ни с чем, если под отказом пусто.

Кнопка выбирается по тому, что обещает САМ текст (DRF-1492 — кнопка ведёт
в ветку, которая отвечает):

* текст зовёт написать словами или прислать ещё фото → «Записать еду»
  (``cb:welcome:food``: «пришлите фото блюда… или напишите название»);
* отказ про УЖЕ существующую запись («этой записи уже нет», «окно возврата
  закрылось») → «Мой дневник»: там видно, что в дневнике на самом деле;
* отказ воды «попробуй через минуту» → «Записать стакан воды» (повтор того
  же действия) ;
* «Меню» — везде: на него отвечают оба пути.

«Мой дневник» ставится только там, где тап дойдёт до дневника
(``personal_surface.diary_is_reachable``), — как в срезе 2.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import NutritionUnavailableError
from apps.orchestrator.personal_surface import CHIP_DIARY, CHIP_WATER
from apps.skills.base import SkillContext
from apps.skills.menu.matching import CALLBACK_MENU_HELP

LOG_FOOD = "cb:welcome:food"
DIARY = CHIP_DIARY["callback"]
WATER = CHIP_WATER["callback"]
MENU = CALLBACK_MENU_HELP
LOG_ID = "log-2267c"


def _callbacks(result: Any) -> list[str]:
    return [b["callback"] for b in (result.action_data or {}).get("buttons") or []]


@pytest.fixture
def nutrition_on(settings):
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True
    return settings


@pytest.fixture
def diary_open(monkeypatch):
    monkeypatch.setattr(
        "apps.orchestrator.personal_surface.personal_records_consent_open", lambda _u: True
    )
    monkeypatch.setattr("apps.consent.nutrition.diary_is_granted", lambda _u: True)


@pytest.fixture
def reachable(monkeypatch):
    monkeypatch.setattr("apps.orchestrator.personal_surface.diary_is_reachable", lambda: True)


def _ctx(text: str, *, photo: bytes | None = None) -> SkillContext:
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2267c"
    bot_user.pk = 2267
    conversation = SimpleNamespace(id="conv-2267c", skill_state={}, last_photo_bytes=photo)
    return SkillContext(
        conversation=conversation,  # type: ignore[arg-type]
        bot_user=bot_user,
        message_text=text,
        has_attachments=photo is not None,
    )


# ── вода ─────────────────────────────────────────────────────────────────


class TestWaterRefusalsHaveAWayOn:
    def _run(self, exc: Exception) -> Any:
        from apps.skills.water.skill import WaterSkill

        client = Mock()

        async def _add_water(**kwargs):
            raise exc

        client.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=client):
            return WaterSkill().handle(_ctx("стакан воды"))

    def test_catalogue_down_offers_the_same_step_again(self, nutrition_on, diary_open) -> None:
        from apps.skills.water.skill import _AYLA_DOWN_FALLBACK

        result = self._run(NutritionUnavailableError("down"))

        assert result.reply_text == _AYLA_DOWN_FALLBACK  # положительная пара: текст отказа на месте
        assert _callbacks(result) == [WATER, MENU]

    def test_nutrition_off_offers_the_menu(self, settings, diary_open) -> None:
        settings.NUTRITION_ENABLED = False
        result = self._run(NutritionUnavailableError("down"))

        assert _callbacks(result) == [MENU]


# ── фото ─────────────────────────────────────────────────────────────────


def _scanner_photo(exc: Exception | None = None, *, photo_bytes: bytes | None = b"jpeg") -> Any:
    """Ход с фото: распознавание падает переданной ошибкой."""
    from apps.skills.food_scanner import skill as scanner

    # Фото приходит так же, как от канала: байты лежат на разговоре
    # (``_extract_photo_bytes``), ход без текста и с вложением.
    ctx = _ctx("", photo=photo_bytes)

    def _scan_and_read(*args, **kwargs):
        raise exc if exc is not None else AssertionError("не ожидалось")

    with patch.object(scanner, "_scan_and_read_diary", _scan_and_read):
        return scanner.FoodScannerSkill().handle(ctx)


class TestPhotoRefusalsOfferTheTextTheyName:
    @pytest.mark.parametrize(
        ("exc_name", "text_name"),
        [
            ("FoodNotRecognizedError", "NOT_RECOGNIZED_FALLBACK"),
            ("ScanDailyLimitError", "SCAN_DAILY_LIMIT_FALLBACK"),
            ("ScanBudgetExhaustedError", "SCAN_BUDGET_EXHAUSTED_FALLBACK"),
            ("NutritionUnavailableError", "AYLA_DOWN_FALLBACK"),
        ],
    )
    def test_each_refusal_offers_to_log_it_another_way(
        self, nutrition_on, diary_open, exc_name, text_name
    ) -> None:
        from apps.integrations import ayla
        from apps.skills.food_scanner import skill as scanner

        exc_cls = getattr(ayla, exc_name)
        result = _scanner_photo(exc_cls("нет"))

        assert result.reply_text == getattr(scanner, text_name)  # текст отказа не меняется
        assert _callbacks(result) == [LOG_FOOD, MENU]

    def test_a_photo_that_did_not_download_asks_for_it_again(
        self, nutrition_on, diary_open
    ) -> None:
        from apps.skills.food_scanner.skill import PHOTO_NO_BYTES

        result = _scanner_photo(photo_bytes=None)

        assert result.reply_text == PHOTO_NO_BYTES
        assert _callbacks(result) == [LOG_FOOD, MENU]


# ── запись текстом ───────────────────────────────────────────────────────


class _Catalogue:
    def __init__(self, *, refuse: Exception) -> None:
        self.refuse = refuse

    async def estimate_food(self, **kwargs):
        raise self.refuse

    async def log_meal(self, **kwargs):
        raise self.refuse

    async def delete_meal(self, **kwargs):
        raise self.refuse

    async def restore_meal(self, **kwargs):
        raise self.refuse

    async def update_meal(self, **kwargs):
        raise self.refuse


def _entry_turn(text: str, refuse: Exception) -> Any:
    from apps.skills.food_clarify.skill import FoodClarifySkill

    ctx = _ctx(text)
    skill = FoodClarifySkill()
    assert skill.matches(ctx), text
    with patch(
        "apps.skills.food_clarify.text_entry.get_nutrition_client",
        return_value=_Catalogue(refuse=refuse),
    ):
        return skill.handle(ctx)


class TestEntryRefusalsPointAtTheDiary:
    @pytest.mark.parametrize(
        ("exc_name", "text_name"),
        [
            ("MealRestoreExpiredError", "RESTORE_EXPIRED_TEXT"),
            ("MealNotFoundError", "ENTRY_GONE_TEXT"),
            ("MealEditConflictError", "ENTRY_WATER_TEXT"),
            ("NutritionUnavailableError", "EDIT_UNAVAILABLE_TEXT"),
        ],
    )
    def test_a_refusal_about_an_existing_entry_offers_the_diary(
        self, nutrition_on, diary_open, reachable, exc_name, text_name
    ) -> None:
        from apps.integrations import ayla
        from apps.skills.food_clarify import text_entry

        exc_cls = getattr(ayla, exc_name)
        result = _entry_turn(f"cb:food:entry_del:{LOG_ID}", exc_cls("нет"))

        assert result.reply_text == getattr(text_entry, text_name)
        assert _callbacks(result) == [DIARY, MENU]

    def test_off_the_global_path_only_the_menu(self, nutrition_on, diary_open) -> None:
        """«Мой дневник» не ставится там, где тап до дневника не дойдёт."""
        from apps.integrations.ayla import MealNotFoundError

        result = _entry_turn(f"cb:food:entry_del:{LOG_ID}", MealNotFoundError("нет"))

        assert _callbacks(result) == [MENU]
