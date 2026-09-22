"""Бот при стойком отказе распознавателя — честно и с дорогой (DRF-2318).

Живой проход 22.09: фото → «Сервис распознавания временно недоступен —
попробуй через минуту», а на деле у распознавателя не оплачен счёт. Через
минуту ничего не изменится. Теперь стойкий отказ получает свой текст без
«через минуту» и кнопку «Записать словами» — дорогу, которая работает сейчас.
Текст — черновик владельцу.

* k1 — стойкий отказ: свой текст, «через минуту» нет, кнопка «Записать словами»;
* k2 — тап кнопки ведёт в запись текстом: спрашивает, что было, и НЕ
  оценивает старую фразу, если та лежала в состоянии;
* k3 — положительная пара: временный отказ по-прежнему «через минуту».
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from apps.integrations.ayla.nutrition_client import NutritionUnavailableError, ScanProviderDownError
from apps.skills.food_clarify import text_entry
from apps.skills.food_scanner import skill as fs
from apps.skills.food_scanner.tests.test_budget_copy_2195 import _client_raising, _handle
from apps.skills.food_scanner.tests.test_skill import (  # переиспользуем стенд
    _context,
    _diary_consent_granted,  # noqa: F401 — autouse
    _enable_nutrition,  # noqa: F401 — autouse
    _personal_data_granted,  # noqa: F401 — autouse
)

pytestmark = pytest.mark.django_db


def _scan_ctx():
    ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
    ctx.conversation.skill_state = {}
    return ctx


class TestK1HonestCopyWithAWay:
    def test_permanent_refusal_has_its_own_text_and_the_words_button(self) -> None:
        result = _handle(_scan_ctx(), _client_raising(ScanProviderDownError("billing_not_active")))

        assert result.reply_text == fs.SCAN_PROVIDER_DOWN_FALLBACK
        assert result.meta["reply_kind"] == "food_scanner_provider_down"
        assert "словам" in result.reply_text
        assert "через минуту" not in result.reply_text
        # Через композер — ровно то, что уйдёт в канал: словарь без ``label``
        # он пропустил бы молча, и кнопки у человека не было бы (ревью #1997).
        from apps.orchestrator.composer import _render_keyboard

        (button,) = _render_keyboard(result.action_data)
        assert button["label"] == "Записать словами"
        assert button["callback"] == "cb:food:diary"


class TestK2TheButtonLeadsToTextEntry:
    def test_a_stale_phrase_is_forgotten(self) -> None:
        ctx = _scan_ctx()
        ctx.conversation.skill_state[text_entry.STATE_KEY] = {
            "source": "борщ 300 г",
            "at": datetime.now(timezone.utc).isoformat(),
        }

        _handle(ctx, _client_raising(ScanProviderDownError("billing_not_active")))

        assert text_entry.STATE_KEY not in ctx.conversation.skill_state
        # Тап «Записать словами» без старой фразы — вопрос «что было».
        with patch("apps.skills.food_clarify.text_entry._gate", return_value=None):
            asked = text_entry.on_diary_tap(ctx)
        assert asked.reply_text == text_entry.ASK_WHAT_TEXT


class TestK3PositivePairTemporaryStaysAsItWas:
    def test_temporary_unavailability_still_says_a_minute(self) -> None:
        result = _handle(_scan_ctx(), _client_raising(NutritionUnavailableError("503")))
        assert result.reply_text == fs.AYLA_DOWN_FALLBACK
        assert "через минуту" in result.reply_text
