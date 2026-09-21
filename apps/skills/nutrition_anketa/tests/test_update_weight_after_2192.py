"""«Обнови вес» после каталожных DRF-2192 / DRF-2193 (beautygo_backend #525).

Каталог изменил ответ на пересчёт, и бот должен читать новый ответ:

* DRF-2192 — на строке ``ayla_calculated`` новый вес больше НЕ ставит
  ``ayla_proposed`` поверх действующего. Действующее остаётся, новое
  предложение лежит рядом в ``targets_provenance.pending_proposal``, и
  ``confirm_targets`` его забирает. Бот до этой правки смотрел только на
  ``targets_source``: карточка после «мой вес 65» говорила «Готово,
  посчитала» прежними числами и не давала кнопки подтверждения — новое
  предложение было невидимо и неподтверждаемо.
* DRF-2193 — при ручном ориентире (``user_entered``) каталог записывает вес и
  ориентир человека не трогает. Отступление DRF-2139 («на ``user_entered``
  вес не пишу, пересчёт бы заменил ориентир») снимается: вес пишется.

* p1 — ``ayla_calculated`` + вес: карточка — «предлагаю» с НОВЫМИ числами из
  ``pending_proposal``, прежний ориентир назван действующим до подтверждения,
  кнопка подтверждения есть;
* p2 — без ``pending_proposal`` в ответе (каталог старый / предложения нет)
  карточка прежняя — ничего не выдумываем;
* m1 — ``user_entered`` + вес: POST уходит (вес + утверждение согласия, без
  полей анкеты — ручной ориентир снимка не имеет), ответ «вес записан,
  ориентир твой прежний», кнопки подтверждения нет.

Узлы ревью:

* k1 — пересчитана только вода: карточка не сравнивает её с калориями,
  действующие калории названы «без изменений»;
* k2 — пересчитаны только калории: действующая вода остаётся на карточке;
* m2 — ручной ориентир без согласия на расчёт: сказать про вес, а не про
  «расчёт пока не запускаю», и ничего не отправить;
* a1 — анкета при ручном ориентире начинается с одной фразы, что анкета его
  не заменит; при расчёте фразы нет; падение чтения — фразы нет, анкета
  идёт.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from apps.consent.personal_calculation import NOT_GRANTED, ConsentAttestationUnavailable
from apps.skills.nutrition_anketa.skill import (
    ANKETA_OVER_MANUAL_NOTE,
    CB_CONFIRM_TARGETS,
    NutritionAnketaSkill,
)
from apps.skills.nutrition_anketa.tests.test_update_weight_2139 import (
    _calculated,
    _callbacks,
    _catalog_after_2192,
    _Run,
)


def _calculated_with_pending(weight: int):
    """Ответ каталога после #525 — из того же источника, что мок w1."""
    return _catalog_after_2192(weight)


class TestP1TheProposalBesideTheActingTarget:
    def test_card_shows_the_new_numbers_and_the_confirm_button(self) -> None:
        run = _Run(profile=_calculated(), upsert=_calculated_with_pending(65))
        card = run.turn("мой вес 65")

        # Присутствие: пересчёт ушёл.
        assert len(run.posted) == 1
        assert "Предлагаю" in card.reply_text
        assert "1700" in card.reply_text
        # Прежний ориентир действует, пока новый не подтверждён (§63).
        assert "1800" in card.reply_text
        assert "действует" in card.reply_text
        assert CB_CONFIRM_TARGETS in _callbacks(card)
        assert "Готово, посчитала" not in card.reply_text


class TestP2NoPendingNoInvention:
    def test_without_a_pending_proposal_the_card_is_the_acting_one(self) -> None:
        confirmed = replace(
            _calculated_with_pending(65),
            raw={
                "norms": {"daily_kcal": 1800},
                "targets_provenance": {"source": "ayla_calculated"},
            },
        )
        run = _Run(profile=_calculated(), upsert=confirmed)
        card = run.turn("мой вес 65")
        # Присутствие: карточка есть — и она про действующее.
        assert card.reply_text
        assert "Предлагаю" not in card.reply_text
        assert CB_CONFIRM_TARGETS not in _callbacks(card)


class TestM1WeightOverAManualTarget:
    def test_weight_is_written_and_the_manual_target_stays(self) -> None:
        manual = _calculated(source="user_entered", snapshot={})
        run = _Run(profile=manual, upsert=replace(manual, weight_kg=65))
        result = run.turn("мой вес 65")

        assert len(run.posted) == 1
        body = run.posted[0]["data"]
        assert body["weight_kg"] == 65
        assert body["consent"]["type"] == "personal_calculation"
        # Ручной ориентир снимка не имеет — полей анкеты бот не выдумывает.
        assert set(body) == {"weight_kg", "consent"}
        # Вес записан — не прежний отказ «ориентир не пересчитываю».
        assert "Записала вес — 65 кг" in result.reply_text
        assert "остаётся прежним" in result.reply_text
        assert result.meta["reply_kind"] == "anketa_update_weight_manual_saved"
        assert CB_CONFIRM_TARGETS not in _callbacks(result)


def _with_pending(pending_update: dict, *, water_ml: int = 2000):
    base = _catalog_after_2192(65)
    pending = {**base.raw["targets_provenance"]["pending_proposal"], **pending_update}
    raw = {
        **base.raw,
        "targets_provenance": {**base.raw["targets_provenance"], "pending_proposal": pending},
    }
    return replace(base, water_ml=water_ml, raw=raw)


class TestK1K2OnlyTheRecomputedKinds:
    def test_only_water_pending_is_not_compared_with_calories(self) -> None:
        answer = _with_pending(
            {
                "kinds": ["fluids"],
                "daily_kcal": None,
                "daily_protein_g": None,
                "daily_fat_g": None,
                "daily_carbs_g": None,
                "daily_water_ml": 2200,
            }
        )
        card = _Run(profile=_calculated(), upsert=answer).turn("мой вес 65")
        assert "2200" in card.reply_text
        assert "вода 2000 мл" in card.reply_text
        assert "1800 ккал в день" not in card.reply_text
        assert "Без изменений" in card.reply_text and "1800" in card.reply_text

    def test_only_calories_pending_keeps_the_acting_water_on_the_card(self) -> None:
        card = _Run(profile=_calculated(), upsert=_with_pending({})).turn("мой вес 65")
        assert "1700" in card.reply_text
        assert "1800 ккал в день" in card.reply_text
        assert "Без изменений" in card.reply_text
        assert "2000 мл" in card.reply_text


class TestM2ManualWithoutConsent:
    def test_says_it_about_the_weight_and_sends_nothing(self) -> None:
        manual = _calculated(source="user_entered", snapshot={})
        run = _Run(profile=manual, attestation=ConsentAttestationUnavailable(NOT_GRANTED))
        result = run.turn("мой вес 65")
        assert result.meta["reply_kind"] == "anketa_update_weight_manual_no_consent"
        assert "вес не записала" in result.reply_text
        assert "остаётся прежним" in result.reply_text
        assert "не запускаю" not in result.reply_text
        assert run.posted == []


class TestA1AnketaOverAManualTarget:
    def _enter(self, probe):
        run = _Run(profile=None)
        with (
            patch.object(NutritionAnketaSkill, "_has_manual_target", probe),
            patch("apps.consent.personal_calculation.is_granted", return_value=True),
        ):
            return run.turn("/anketa")

    def test_manual_target_prepends_one_sentence(self) -> None:
        result = self._enter(lambda self, ctx: True)
        assert result.reply_text.startswith(ANKETA_OVER_MANUAL_NOTE)

    def test_calculation_has_no_sentence(self) -> None:
        result = self._enter(lambda self, ctx: False)
        # Присутствие: анкета началась — вопрос есть.
        assert result.reply_text
        assert ANKETA_OVER_MANUAL_NOTE not in result.reply_text
