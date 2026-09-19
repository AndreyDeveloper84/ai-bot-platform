"""DRF-2108 — чипы под записью по происхождению; окно возврата с провода.

Хвост F4 (DRF-1838) по замеру на dev:

* под записью по ФОТО чипов не было вовсе; теперь — только «Удалить»:
  «Исправить граммы» (÷100) верно лишь для записи текстом, тот же предикат,
  что ``isTextEntry`` в Mini App; без ``entry_origin`` — тоже не предлагаем;
* «Вернуть можно в течение 15 минут» было константой бота, хотя каталог
  отдаёт ``restore_window_expires_at`` в ответе на удаление; теперь минуты
  считаются с провода, а без поля обещания нет — ни фразы, ни чипа.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import FoodLogResponse, MealDeletion
from apps.orchestrator.ui.keyboards import food_entry_keyboard
from apps.skills.base import SkillContext
from apps.skills.food_clarify.skill import FoodClarifySkill
from apps.skills.food_clarify.text_entry import restore_minutes_left
from apps.skills.food_scanner.skill import FoodScannerSkill

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
LOG_ID = "log-photo-1"


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True


@pytest.fixture
def consent():
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ),
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield


@pytest.fixture
def clock():
    with patch("apps.skills.food_clarify.text_entry._now_utc", return_value=NOW):
        yield NOW


def _ctx(text: str) -> SkillContext:
    conversation = SimpleNamespace(id="conv-2108", skill_state={})
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2108"
    bot_user.pk = 2108
    return SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)  # type: ignore[arg-type]


def _log(origin: str | None, **extra: Any) -> FoodLogResponse:
    raw = {} if origin is None else {"entry_origin": origin}
    return FoodLogResponse(
        log_id=extra.get("log_id", LOG_ID),
        dish_name="Борщ",
        meal_type="other",
        calories=250.0,
        raw=raw,
    )


class _Catalogue:
    def __init__(self, *, restored: FoodLogResponse, expires_at: str | None) -> None:
        self.restored = restored
        self.expires_at = expires_at

    async def delete_meal(self, **kwargs):
        return MealDeletion(log_id=kwargs["log_id"], restore_window_expires_at=self.expires_at)

    async def restore_meal(self, **kwargs):
        return self.restored


def _turn(text: str, catalogue: _Catalogue):
    skill = FoodClarifySkill()
    ctx = _ctx(text)
    assert skill.matches(ctx), text
    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=catalogue):
        return skill.handle(ctx)


def _callbacks(result) -> list[str]:
    return [b["callback"] for b in (result.action_data or {}).get("buttons") or []]


# ─── чипы по происхождению ───────────────────────────────────────────────────


class TestChipsByOrigin:
    def test_keyboard_without_fix_has_only_delete(self) -> None:
        both = food_entry_keyboard("x", fixable=True)
        only = food_entry_keyboard("x", fixable=False)
        assert [b["callback"] for b in both] == ["cb:food:entry_fix:x", "cb:food:entry_del:x"]
        assert [b["callback"] for b in only] == ["cb:food:entry_del:x"]

    def test_a_photo_entry_carries_only_the_delete_chip_with_its_id(
        self, consent, settings
    ) -> None:
        settings.FOOD_PHOTO_SCAN_ENABLED = True
        ctx = _ctx("cb:food:to_diary:scan-2108")
        client = Mock()

        async def _log_meal(**kwargs):
            return _log("photo_estimated_confirmed", log_id=LOG_ID)

        client.log_meal = _log_meal
        with patch("apps.skills.food_scanner.skill.get_nutrition_client", return_value=client):
            result = FoodScannerSkill().handle(ctx)

        assert result.action_type == "food_logged"
        assert (result.action_data or {})["log_id"] == LOG_ID
        assert _callbacks(result) == [f"cb:food:entry_del:{LOG_ID}"]

    def test_restoring_a_photo_entry_offers_delete_but_not_fix(self, consent, clock) -> None:
        catalogue = _Catalogue(restored=_log("photo_user_corrected"), expires_at=None)

        result = _turn(f"cb:food:entry_undo:{LOG_ID}", catalogue)

        assert result.reply_text.startswith("Вернула в дневник: Борщ")
        assert _callbacks(result) == [f"cb:food:entry_del:{LOG_ID}"]

    def test_restoring_an_entry_without_origin_does_not_offer_fix(self, consent, clock) -> None:
        catalogue = _Catalogue(restored=_log(None), expires_at=None)

        result = _turn(f"cb:food:entry_undo:{LOG_ID}", catalogue)

        assert result.reply_text.startswith("Вернула в дневник: Борщ")
        assert _callbacks(result) == [f"cb:food:entry_del:{LOG_ID}"]

    def test_restoring_a_text_entry_keeps_both_chips(self, consent, clock) -> None:
        catalogue = _Catalogue(restored=_log("text_estimated_confirmed"), expires_at=None)

        result = _turn(f"cb:food:entry_undo:{LOG_ID}", catalogue)

        assert _callbacks(result) == [f"cb:food:entry_fix:{LOG_ID}", f"cb:food:entry_del:{LOG_ID}"]


# ─── окно возврата с провода ────────────────────────────────────────────────


class TestRestoreWindowFromTheWire:
    @pytest.mark.parametrize(
        ("expires_at", "expected"),
        [
            ((NOW + timedelta(minutes=14, seconds=30)).isoformat(), 15),
            ((NOW + timedelta(minutes=1)).isoformat(), 1),
            ((NOW - timedelta(seconds=1)).isoformat(), None),
            (None, None),
            ("not-a-date", None),
        ],
    )
    def test_minutes_left(self, expires_at, expected) -> None:
        assert restore_minutes_left(expires_at, now=NOW) == expected

    def test_deletion_names_the_minutes_from_the_wire(self, clock) -> None:
        catalogue = _Catalogue(
            restored=_log("text_estimated_confirmed"),
            expires_at=(NOW + timedelta(minutes=14, seconds=30)).isoformat(),
        )

        result = _turn(f"cb:food:entry_del:{LOG_ID}", catalogue)

        assert result.reply_text == "Убрала запись из дневника. Вернуть можно ещё 15 минут."
        assert _callbacks(result) == [f"cb:food:entry_undo:{LOG_ID}"]

    def test_deletion_without_the_field_makes_no_promise(self, clock) -> None:
        catalogue = _Catalogue(restored=_log("text_estimated_confirmed"), expires_at=None)

        result = _turn(f"cb:food:entry_del:{LOG_ID}", catalogue)

        assert result.action_type == "food_entry_deleted"
        assert result.reply_text == "Убрала запись из дневника."
        assert "Вернуть" not in result.reply_text
        assert _callbacks(result) == []
