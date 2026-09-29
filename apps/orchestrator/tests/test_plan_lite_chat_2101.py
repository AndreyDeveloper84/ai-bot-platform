"""«Мой план» в чате и в меню — из того же документа, что и Mini App (DRF-2101).

* текст «мой план» забирается ДЕТЕРМИНИРОВАННО в лестнице глобального пути
  (рядом с чтением дневника DRF-1302, тот же двухслойный приём) и рисует
  карточку из ``wellness-context.plan_lite``: сверху — цель словами самого
  человека, без нашего ярлыка (DRF-2283), ниже «На этой неделе: записаться
  на услугу ✓/—, дневник N из M, вода N из M»;
* без плана — предложение Ayla из шаблона цели (DRF-2125,
  ``test_plan_chat_2125``); без шаблона — «плана пока нет», один раз и только
  когда человек сам спросил (без проактивности, ``WELLNESS_PROACTIVE_ENABLED``
  заперт);
* без флага ``PLAN_LITE_ENABLED`` текст — не наш (``None`` → модель, как
  раньше); состав главного меню (§37, OD-UI-2) флаг не меняет — кнопки
  «Мой план» нет ни с ним, ни без него (решение о девятом пункте — за
  владельцем);
* В-5: в карточке нет ни процента, ни «цель достигнута».
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla.wellness_context_client import (
    PlanLite,
    PlanLiteAction,
    WellnessContext,
    WellnessContextUnavailableError,
)
from apps.orchestrator.nutrition_global import try_handle_structured_nutrition_turn
from apps.orchestrator.plan_lite_card import MY_PLAN_TRIGGERS, PLAN_LITE_COPY

pytestmark = pytest.mark.django_db(transaction=True)

PLAN = PlanLite(
    plan_id="p-1",
    goal_key="tone_up",
    actions=(
        PlanLiteAction("book_service", "per_week", 1, 1, "2026-09-14", "2026-09-21"),
        PlanLiteAction("log_food", "per_week", 5, 3, "2026-09-14", "2026-09-21"),
        PlanLiteAction("log_water", "per_day", 7, 4, "2026-09-18", "2026-09-19"),
    ),
)


@pytest.fixture(autouse=True)
def _flags(settings):
    settings.NUTRITION_ENABLED = True
    settings.PLAN_LITE_ENABLED = True
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "svc"  # noqa: S105  # pragma: allowlist secret


def _bot_user() -> Mock:
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2101"
    bot_user.pk = 7
    return bot_user


def _turn(text: str, ctx, *, proposal=None):
    fake = Mock()
    if isinstance(ctx, Exception):
        fake.get_wellness_context.side_effect = ctx
    else:
        fake.get_wellness_context.return_value = ctx
    if isinstance(proposal, Exception):
        fake.get_plan_lite_proposal.side_effect = proposal
    else:
        fake.get_plan_lite_proposal.return_value = proposal
    with patch("apps.orchestrator.plan_lite_card.WellnessContextHttpClient", return_value=fake):
        result = try_handle_structured_nutrition_turn(
            text=text,
            attachments=None,
            bot_user=_bot_user(),
            conversation=SimpleNamespace(id="c", skill_state={}),
            trace_id="t-2101",
        )
    return result, fake


class TestMyPlanInChat:
    def test_my_plan_text_renders_the_card_from_the_document(self) -> None:
        result, fake = _turn(
            "мой план", WellnessContext(has_plan=False, gated=True, plan_lite=PLAN)
        )

        assert result is not None, "«мой план» ушёл бы модели"
        assert fake.get_wellness_context.call_args.kwargs == {"external_user_id": "bot:max:2101"}
        text = result.reply_text
        # Ярлыка над целью больше нет (DRF-2283, слово владельца 24.09):
        # сверху стоит ровно то, чем цель названа в документе, и ничего
        # нашего. Узел этим не ослаб — стало строже, чем «есть подстрока».
        assert text.splitlines()[0] == "tone_up", text
        assert "записаться на услугу ✓" in text
        assert "дневник 3 из 5" in text
        assert "вода 4 из 7" in text
        assert result.meta["reply_kind"] == "plan_lite_card"

    @pytest.mark.parametrize("text", ["Мой план", "покажи мой план", "какой у меня план"])
    def test_other_phrasings_land_on_the_same_card(self, text: str) -> None:
        result, _ = _turn(text, WellnessContext(has_plan=False, gated=True, plan_lite=PLAN))
        assert result is not None and result.meta["reply_kind"] == "plan_lite_card"

    def test_every_declared_trigger_is_a_phrase_not_a_word(self) -> None:
        """Нижняя граница матчера: триггеры названы и не равны «план» —
        одно слово забирало бы «план массажа» у модели."""
        assert MY_PLAN_TRIGGERS and all("план" in t and t != "план" for t in MY_PLAN_TRIGGERS)

    def test_the_card_carries_no_result_wording(self) -> None:
        """В-5: только «N из M» — ни процента, ни «достигнута», ни «пропустил»."""
        result, _ = _turn("мой план", WellnessContext(has_plan=False, gated=True, plan_lite=PLAN))
        low = result.reply_text.lower()
        # Присутствие раньше отсутствия: карточка с фактами «N из M» на месте.
        assert "3 из 5" in low and "4 из 7" in low
        assert "%" not in low
        assert "достиг" not in low and "пропуст" not in low and "прогресс" not in low

    def test_my_plan_without_a_plan_and_without_a_template_says_so_without_nagging(self) -> None:
        """DRF-2125: без плана чат читает предложение; когда шаблона у цели нет —
        прежняя строка «составить можно в приложении» (полный ход — test_plan_chat_2125)."""
        from apps.integrations.ayla.wellness_context_client import PlanLiteNoTemplateError

        result, fake = _turn(
            "мой план",
            WellnessContext(has_plan=False, gated=True, plan_lite=None),
            proposal=PlanLiteNoTemplateError("no template"),
        )
        assert result is not None
        assert fake.get_plan_lite_proposal.call_count == 1
        assert result.reply_text == PLAN_LITE_COPY.no_plan
        assert result.meta["reply_kind"] == "plan_lite_none"

    def test_ayla_unavailable_is_named_not_an_empty_plan(self) -> None:
        result, _ = _turn("мой план", WellnessContextUnavailableError("down"))
        assert result is not None
        assert result.reply_text == PLAN_LITE_COPY.unavailable
        assert result.meta["reply_kind"] == "plan_lite_unavailable"

    def test_my_plan_without_the_flag_is_not_ours(self, settings) -> None:
        settings.PLAN_LITE_ENABLED = False
        result, fake = _turn("мой план", WellnessContext(has_plan=False, plan_lite=PLAN))
        assert result is None
        assert fake.get_wellness_context.call_count == 0

    def test_other_text_is_not_ours(self) -> None:
        result, fake = _turn("покажи мастеров", WellnessContext(has_plan=False, plan_lite=PLAN))
        assert result is None
        assert fake.get_wellness_context.call_count == 0


class TestMenuIsUntouched:
    @pytest.mark.parametrize("flag", [True, False], ids=["on", "off"])
    def test_the_main_menu_composition_does_not_change_with_the_flag(
        self, settings, flag: bool
    ) -> None:
        """Состав главного меню — решение владельца (§37, OD-UI-2): пищевой
        пункт — единственный условный; Plan Lite кнопки не добавляет.
        Вход в план — текстом «мой план» в чате и экраном Mini App."""
        from apps.skills.menu.marketplace import main_items

        settings.PLAN_LITE_ENABLED = flag
        settings.MAX_BOT_WEB_APP = "aylabot"
        labels = [item.label for item in main_items(bot_user=_bot_user())]
        # Присутствие раньше отсутствия: меню собрано, «Помощь» замыкает его.
        assert "Помощь" in labels, labels
        assert "Мой план" not in labels, labels
        assert labels == [item.label for item in main_items(bot_user=_bot_user())]
