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
"""

from __future__ import annotations

from dataclasses import replace

from apps.skills.nutrition_anketa.skill import CB_CONFIRM_TARGETS
from apps.skills.nutrition_anketa.tests.test_skill import _profile
from apps.skills.nutrition_anketa.tests.test_update_weight_2139 import (
    _SNAPSHOT,
    _calculated,
    _callbacks,
    _Run,
)


def _calculated_with_pending(weight: int):
    """Ответ каталога после #525: действующее + предложение рядом."""
    return replace(
        _profile(),
        weight_kg=weight,
        targets_source="ayla_calculated",
        targets_input_snapshot=dict(_SNAPSHOT),
        raw={
            "norms": {"daily_kcal": 1800},
            "targets_provenance": {
                "source": "ayla_calculated",
                "input_snapshot": dict(_SNAPSHOT),
                "pending_proposal": {
                    "kinds": ["calories"],
                    "daily_kcal": 1700,
                    "daily_protein_g": 110,
                    "daily_fat_g": 60,
                    "daily_carbs_g": 180,
                    "input_snapshot": {**_SNAPSHOT, "weight_kg": weight},
                    "method_versions": {"calories": "mifflin_st_jeor_v2"},
                    "computed_at": "2026-09-21T09:00:00.000Z",
                    "goal": "maintain",
                    "pace": "moderate",
                    "goal_overridden_by": None,
                    "overrides_applied": [],
                },
            },
        },
    )


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
            raw={"norms": {"daily_kcal": 1800}, "targets_provenance": {"source": "ayla_calculated"}},
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
        assert "65" in result.reply_text
        assert "не пересчитываю" not in result.reply_text
        assert result.meta["reply_kind"] == "anketa_update_weight_manual_saved"
        assert CB_CONFIRM_TARGETS not in _callbacks(result)
