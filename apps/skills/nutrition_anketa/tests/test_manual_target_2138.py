"""Ориентир от специалиста в чате — режим 3 §82 (DRF-2138, Анкета-1, В2).

Каталог умеет ручной ориентир давно (``targets/manual/``, источник
``user_entered``); в боте входа не было, и стоп-текст анкеты честно говорил
«такой ручки у меня ещё нет». Теперь входа два, диалог один, число — только
из ввода человека, Ayla ничего не советует: она записывает источник.

* m1 — узел: стоп-ветка → кнопка «Впиши ориентир от специалиста» → «Сколько
  ккал в день назначил специалист?» → 1800 → карточка «Ориентир от
  специалиста: 1800 ккал. Источник — ты, не расчёт Ayla. Подтвердить?» →
  подтвердить → POST ``{calories_kcal: 1800}`` → сводка «ориентир от
  специалиста», без «Считала»; в тексте карточки нет чисел, кроме введённого;
* m2 — фраза «мне врач назначил 1800 ккал» → сразу карточка (число из
  фразы), без вопроса;
* m3 — ложные входы: «1800» / «1800 ккал» (без слова специалиста) /
  «врач назначил 2000 шагов» (единица не ккал) → ``matches`` False; «ориентир
  от специалиста» без числа → вопрос числа;
* m4 — <1000: каталог 422 → отказ словами с порогом ИЗ ОТВЕТА, состояние
  снято, второй POST не ушёл;
* m5 — 1000–1199: ``calories_low`` → фраза «Записала; это низкий ориентир —
  держись под наблюдением специалиста»;
* m6 — 409 ``calories_deviation`` → «Это сильно отличается от расчётного —
  подтверждаешь?» → «Да» → повтор POST с ``confirm_deviation: true``;
  «Нет» — второго POST нет;
* m7 — без PERSONAL_DATA → отказ с кнопкой согласия, POST нет; ворота и на
  входе, и на подтверждении;
* m8 — согласие M (``personal_calculation``) НЕ требуется: без него путь
  работает — ручной ориентир не расчёт;
* m9 — до подтверждения POST нет; «Не сейчас» → состояние снято, POST нет;
* m10 — отзыв: ``user_entered`` без согласия M → предлагается «Отключить и
  удалить» (а не «нечего отключать»); без ручного и без M — как было;
* m11 — не число («abc», «0», слова, минус, два числа) → переспрос, POST
  нет; пороги каталога в боте не дублируются: и 20, и 5000 уходят в каталог
  как есть — отказ ниже порога говорит каталог (m4);
* m12 — стоп-текст больше не говорит «ручки нет», под ним кнопка первой;
* m13 — сводка ``user_entered``: заголовок «Ориентир от специалиста», строки
  методики нет, вода справочная остаётся, «Считала» нет.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla.nutrition_client import (
    ManualTargetsConfirmationRequiredError,
    ManualTargetsRefusedError,
    NutritionUnavailableError,
)
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa import skill as skill_module
from apps.skills.nutrition_anketa.skill import (
    MANUAL_CANCEL_CALLBACK,
    MANUAL_CONFIRM_CALLBACK,
    MANUAL_CONFIRM_DEVIATION_CALLBACK,
    MANUAL_DEVIATION_NO_CALLBACK,
    MANUAL_STATE_KEY,
    MANUAL_TARGET_BUTTON,
    MANUAL_TARGET_CALLBACK,
    WITHDRAW_CALLBACK,
    NutritionAnketaSkill,
    _format_summary,
)
from apps.skills.nutrition_anketa.tests.test_skill import (
    _profile,
    _StatefulConversation,
)

_PD_OPEN = "apps.orchestrator.personal_surface.personal_records_consent_open"
_M_GRANTED = "apps.consent.personal_calculation.is_granted"

_ASK = "Сколько ккал в день назначил специалист?"
_CARD_TAIL = "Источник — ты, не расчёт Ayla. Подтвердить?"
_LOW = "Записала; это низкий ориентир — держись под наблюдением специалиста"
_DEVIATION = "Это сильно отличается от расчётного — подтверждаешь?"


def _manual_profile(kcal: int):
    from dataclasses import replace

    # Каталог стирает посчитанное (макросы, bmr) — набор одного происхождения;
    # справочная вода остаётся (DRF-1929).
    return replace(
        _profile(daily_kcal=kcal),
        protein_g=None,
        fat_g=None,
        carbs_g=None,
        bmr=None,
        targets_source="user_entered",
        targets_method_versions={},
        targets_input_snapshot={},
    )


class _Run:
    """Один навык, одна беседа, перехваченный каталог, оба согласия под рукой."""

    def __init__(
        self,
        state: dict | None = None,
        *,
        pd_open: bool = True,
        m_granted: bool = True,
        manual: Any = None,
        profile: Any = None,
    ) -> None:
        self.conversation = _StatefulConversation(state)
        self.posted: list[dict] = []
        self.pd_open = pd_open
        self.m_granted = m_granted
        client = Mock()
        results = list(manual) if isinstance(manual, list) else None

        async def _set_manual(**kwargs):
            self.posted.append(kwargs)
            outcome = results.pop(0) if results else manual
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is None:
                return _manual_profile(kwargs["calories_kcal"]), {"warnings": []}
            return outcome

        async def _get_profile(**kwargs):
            return profile

        client.set_manual_targets = _set_manual
        client.get_profile = _get_profile
        self._client = client
        self.skill = NutritionAnketaSkill()

    def ctx(self, text: str) -> SkillContext:
        return SkillContext(
            conversation=self.conversation,  # type: ignore[arg-type]
            bot_user=Mock(channel="max", channel_user_id="12345"),
            message_text=text,
        )

    def matches(self, text: str) -> bool:
        with patch(_PD_OPEN, return_value=self.pd_open):
            return self.skill.matches(self.ctx(text))

    def turn(self, text: str):
        with (
            patch(
                "apps.skills.nutrition_anketa.skill.get_nutrition_client",
                return_value=self._client,
            ),
            patch(_PD_OPEN, return_value=self.pd_open),
            patch(_M_GRANTED, return_value=self.m_granted),
        ):
            return self.skill.handle(self.ctx(text))

    @property
    def manual_state(self) -> dict | None:
        return self.conversation.skill_state.get(MANUAL_STATE_KEY)


def _labels(result) -> list[str]:
    return [b["label"] for b in (result.action_data or {}).get("buttons") or []]


def _callbacks(result) -> list[str]:
    return [b["callback"] for b in (result.action_data or {}).get("buttons") or []]


def _numbers(text: str) -> set[str]:
    import re

    return set(re.findall(r"\d+", text))


class TestM1StopBranchToConfirmedTarget:
    def test_the_whole_path(self) -> None:
        run = _Run()
        # Стоп-ветка: возраст 16.
        run.turn("/anketa")
        run.turn("cb:anketa:choice:gender:female")
        stop = run.turn("16")
        assert stop.action_type == "anketa_stop"
        assert _labels(stop)[0] == MANUAL_TARGET_BUTTON
        assert _callbacks(stop)[0] == MANUAL_TARGET_CALLBACK

        asked = run.turn(MANUAL_TARGET_CALLBACK)
        assert asked.reply_text == _ASK
        assert run.manual_state == {"step": "kcal"}
        assert run.posted == []

        card = run.turn("1800")
        assert card.reply_text == f"Ориентир от специалиста: 1800 ккал. {_CARD_TAIL}"
        assert _numbers(card.reply_text) == {"1800"}
        assert _callbacks(card)[:2] == [MANUAL_CONFIRM_CALLBACK, MANUAL_CANCEL_CALLBACK]
        assert run.posted == []  # до подтверждения — ничего

        done = run.turn(MANUAL_CONFIRM_CALLBACK)
        assert run.posted == [{"external_user_id": "bot:max:12345", "calories_kcal": 1800}]
        assert "Ориентир от специалиста: 1800 ккал" in done.reply_text
        assert "Считала" not in done.reply_text
        assert run.manual_state is None
        assert done.meta["reply_kind"] == "anketa_manual_target_done"


class TestM2PhraseWithNumberGoesStraightToTheCard:
    @pytest.mark.parametrize(
        "text",
        [
            "мне врач назначил 1800 ккал",
            "Диетолог назначила 1800 калорий в день",
            "врач сказал 1800",
            "ориентир от специалиста 1800",
        ],
    )
    def test_card_without_a_question(self, text: str) -> None:
        run = _Run()
        assert run.matches(text)
        card = run.turn(text)
        assert card.reply_text.startswith("Ориентир от специалиста: 1800 ккал.")
        assert run.manual_state == {"step": "confirm", "kcal": 1800}
        assert run.posted == []


class TestM3FalseEntries:
    @pytest.mark.parametrize(
        "text",
        [
            "1800",
            "1800 ккал",
            "врач назначил 2000 шагов",
            "врач сказал пить 2000 мл",
            "диетолог назначил 100 г белка",
            "врач назначил",
            "у меня 1800 калорий вышло за день",
            "назначил встречу на 1500",
            "врач назначил 1800 и 2000 через неделю",
        ],
    )
    def test_not_ours(self, text: str) -> None:
        run = _Run()
        # Присутствие раньше отсутствия: матчер живой — фраза с числом наша.
        assert run.matches("мне врач назначил 1800 ккал")
        assert not run.matches(text)

    def test_phrase_without_a_number_asks(self) -> None:
        run = _Run()
        assert run.matches("ориентир от специалиста")
        asked = run.turn("Ориентир от специалиста")
        assert asked.reply_text == _ASK
        assert run.manual_state == {"step": "kcal"}


class TestM4BelowFloorIsRefusedByTheCatalogue:
    def test_refusal_names_the_catalogue_floor_and_clears(self) -> None:
        run = _Run(
            manual=ManualTargetsRefusedError(
                "CALORIES_BELOW_FLOOR", {"calories_kcal": 900, "floor_kcal": 1000}
            )
        )
        run.turn("мне врач назначил 900 ккал")
        refused = run.turn(MANUAL_CONFIRM_CALLBACK)
        assert len(run.posted) == 1
        assert refused.reply_text == (
            "Такой ориентир я записать не могу — ниже 1000 ккал числа должен вести "
            "специалист напрямую."
        )
        assert run.manual_state is None
        assert refused.meta["reply_kind"] == "anketa_manual_target_refused"

    def test_the_floor_in_the_text_is_the_catalogues_not_the_bots(self) -> None:
        run = _Run(
            manual=ManualTargetsRefusedError(
                "CALORIES_BELOW_FLOOR", {"calories_kcal": 900, "floor_kcal": 1200}
            )
        )
        run.turn("мне врач назначил 900 ккал")
        refused = run.turn(MANUAL_CONFIRM_CALLBACK)
        assert "ниже 1200 ккал" in refused.reply_text


class TestM5CaloriesLow:
    def test_low_warning_is_said(self) -> None:
        run = _Run(manual=(_manual_profile(1100), {"warnings": ["calories_low"]}))
        run.turn("мне врач назначил 1100 ккал")
        done = run.turn(MANUAL_CONFIRM_CALLBACK)
        assert _LOW in done.reply_text
        assert "Ориентир от специалиста: 1100 ккал" in done.reply_text


class TestM6Deviation:
    def _deviation(self) -> ManualTargetsConfirmationRequiredError:
        return ManualTargetsConfirmationRequiredError(
            "calories_deviation",
            {"calories_kcal": 1200, "maintenance_kcal": 2100, "deviation_ratio": 0.43},
        )

    def test_yes_repeats_with_the_flag(self) -> None:
        run = _Run(manual=[self._deviation(), None])
        run.turn("мне врач назначил 1200 ккал")
        ask = run.turn(MANUAL_CONFIRM_CALLBACK)
        assert ask.reply_text == _DEVIATION
        assert _callbacks(ask) == [MANUAL_CONFIRM_DEVIATION_CALLBACK, MANUAL_DEVIATION_NO_CALLBACK]
        assert run.manual_state == {"step": "deviation", "kcal": 1200}
        done = run.turn(MANUAL_CONFIRM_DEVIATION_CALLBACK)
        assert run.posted == [
            {"external_user_id": "bot:max:12345", "calories_kcal": 1200},
            {"external_user_id": "bot:max:12345", "calories_kcal": 1200, "confirm_deviation": True},
        ]
        assert "Ориентир от специалиста: 1200 ккал" in done.reply_text

    def test_no_stops_without_a_second_post(self) -> None:
        run = _Run(manual=[self._deviation(), None])
        run.turn("мне врач назначил 1200 ккал")
        run.turn(MANUAL_CONFIRM_CALLBACK)
        no = run.turn(MANUAL_DEVIATION_NO_CALLBACK)
        assert len(run.posted) == 1
        assert run.manual_state is None
        assert no.meta["reply_kind"] == "anketa_manual_target_cancelled"


class TestM7PersonalDataGate:
    def test_entry_without_personal_data_is_refused_with_the_consent_button(self) -> None:
        run = _Run(pd_open=False)
        refused = run.turn("мне врач назначил 1800 ккал")
        assert refused.meta["reply_kind"] == "anketa_manual_target_consent_required"
        assert "согласие" in refused.reply_text.lower()
        assert run.manual_state is None
        assert run.posted == []

    def test_confirm_without_personal_data_does_not_post(self) -> None:
        run = _Run()
        run.turn("мне врач назначил 1800 ккал")
        run.pd_open = False
        refused = run.turn(MANUAL_CONFIRM_CALLBACK)
        assert run.posted == []
        assert refused.meta["reply_kind"] == "anketa_manual_target_consent_required"


class TestM8ConsentMIsNotRequired:
    def test_path_works_without_personal_calculation_consent(self) -> None:
        run = _Run(m_granted=False)
        run.turn("мне врач назначил 1800 ккал")
        done = run.turn(MANUAL_CONFIRM_CALLBACK)
        assert run.posted == [{"external_user_id": "bot:max:12345", "calories_kcal": 1800}]
        assert "Ориентир от специалиста: 1800 ккал" in done.reply_text


class TestM9NothingBeforeConfirmation:
    def test_cancel_clears_and_posts_nothing(self) -> None:
        run = _Run()
        run.turn("мне врач назначил 1800 ккал")
        cancelled = run.turn(MANUAL_CANCEL_CALLBACK)
        assert run.posted == []
        assert run.manual_state is None
        assert cancelled.meta["reply_kind"] == "anketa_manual_target_cancelled"

    def test_unavailable_catalogue_keeps_the_card_pending(self) -> None:
        run = _Run(manual=NutritionUnavailableError("network"))
        run.turn("мне врач назначил 1800 ккал")
        down = run.turn(MANUAL_CONFIRM_CALLBACK)
        assert down.meta["reply_kind"] == "anketa_manual_target_unavailable"
        # Число не потеряно: можно нажать ещё раз.
        assert run.manual_state == {"step": "confirm", "kcal": 1800}


class TestM10WithdrawTouchesTheManualTarget:
    def test_user_entered_without_consent_m_is_offered_the_deletion(self) -> None:
        run = _Run(m_granted=False, profile=_manual_profile(1800))
        ask = run.turn(WITHDRAW_CALLBACK)
        assert ask.action_type == "anketa_withdraw_ask"

    def test_nothing_manual_and_no_consent_m_is_nothing_to_withdraw(self) -> None:
        run = _Run(m_granted=False, profile=_profile())
        ask = run.turn(WITHDRAW_CALLBACK)
        assert ask.meta["reply_kind"] == "anketa_withdraw_nothing"

    def test_no_profile_and_no_consent_m_is_nothing_to_withdraw(self) -> None:
        run = _Run(m_granted=False, profile=None)
        ask = run.turn(WITHDRAW_CALLBACK)
        assert ask.meta["reply_kind"] == "anketa_withdraw_nothing"


class TestM11NotANumber:
    @pytest.mark.parametrize("text", ["abc", "0", "тысяча восемьсот", "-1800", "1800 и 2000"])
    def test_reasks_and_posts_nothing(self, text: str) -> None:
        run = _Run()
        run.turn(MANUAL_TARGET_CALLBACK)
        again = run.turn(text)
        assert again.meta["reply_kind"] == "anketa_manual_target_kcal_invalid"
        assert run.manual_state == {"step": "kcal"}
        assert run.posted == []

    @pytest.mark.parametrize("value", ["20", "5000"])
    def test_no_bot_side_threshold(self, value: str) -> None:
        """Пороги — у каталога; число уходит как есть, отказ скажет каталог."""
        run = _Run()
        run.turn(MANUAL_TARGET_CALLBACK)
        card = run.turn(value)
        assert card.reply_text.startswith(f"Ориентир от специалиста: {value} ккал.")


class TestM12StopTextIsHonestAgain:
    def test_no_more_missing_handle(self) -> None:
        for text in skill_module._STOP_TEXTS.values():
            assert "ручки" not in text
            assert "некуда" not in text

    def test_button_is_first_under_both_stops(self) -> None:
        run = _Run()
        run.turn("/anketa")
        run.turn("cb:anketa:choice:gender:female")
        run.turn("30")
        stop = run.turn("cb:anketa:choice:screening:pregnancy_nursing")
        assert stop.action_type == "anketa_stop"
        assert _labels(stop)[0] == MANUAL_TARGET_BUTTON


class TestM13SummaryForUserEntered:
    def test_header_source_and_no_method_line(self) -> None:
        text = _format_summary(_manual_profile(1800))
        assert text.startswith("Ориентир от специалиста: 1800 ккал")
        assert "Источник — ты, не расчёт Ayla" in text
        assert "Считала" not in text and "методик" not in text.lower()
        assert "Предлагаю" not in text
        assert _numbers(text) <= {"1800", "2100"}  # введённое и справочная вода из профиля
