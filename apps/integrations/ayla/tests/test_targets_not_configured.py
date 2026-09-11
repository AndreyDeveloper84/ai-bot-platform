"""DRF-1686 (OD-NUT-1, §6 свода 11.09): неизвестная норма — NOT_CONFIGURED, не число.

Сторож стоит на **типе**, а не на разборе ответа: ``ProfileResponse`` не
способен нести ориентир без названного происхождения, как бы его ни собрали.
Поэтому проверки ниже строят DTO руками — самым коротким путём в обход
клиента, — и именно этот путь обязан быть перекрыт.

Второй предмет — что ни одна поверхность не печатает число из профиля,
которому §103 запрещает его показывать. Сторож формулируется положительно
и с числом на обоих концах: тот же профиль с теми же тремя числами, как
``unknown_legacy`` — ни одного из них нигде; как ``ayla_calculated`` — все
три. Без второй половины первая была бы зелёной и у рендера, который не
печатает ориентиров вовсе.
"""

from __future__ import annotations

import re

import pytest

from apps.integrations.ayla.nutrition_client import (
    TARGETS_CONFIGURED,
    TARGETS_NOT_CONFIGURED,
    ProfileResponse,
    SummaryResponse,
    WaterTodayResponse,
    targets_configured,
)
from apps.nutrition_proactive.render import goal_remark, render_daily_report

#: Три числа, которых у не настроенного профиля не должно быть видно нигде.
#: Выбраны так, чтобы не совпадать ни с чем, что рендер печатает сам
#: (съедено, выпито, дни).
PROTEIN, KCAL, WATER = 95, 1850, 2100


def _profile(source: str) -> ProfileResponse:
    return ProfileResponse(
        gender="female",
        age=31,
        height_cm=168,
        weight_kg=62,
        goal="lose",
        daily_kcal=KCAL,
        protein_g=PROTEIN,
        fat_g=60,
        carbs_g=210,
        water_ml=WATER,
        bmr=1400,
        health_flags={},
        disclaimer_acked=None,
        targets_source=source,
    )


def _summary() -> SummaryResponse:
    # ``calories_goal`` в сводке — число БЕЗ происхождения: у unknown_legacy
    # каталог до команды очистки присылает его. Оно и есть предмет.
    return SummaryResponse(
        date="2026-09-11",
        calories_total=640.0,
        calories_goal=KCAL,
        protein_g=31.0,
        fat_g=22.0,
        carbs_g=70.0,
        entries=[{"id": "e1"}],
        raw={},
    )


def _water() -> WaterTodayResponse:
    return WaterTodayResponse(total_ml=700, norm_ml=WATER, entries=[], raw={})


def _numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"\d+", text)}


class TestTheTypeCannotCarryAnUnexplainedTarget:
    @pytest.mark.parametrize(
        "source",
        ["none", "unknown_legacy", "", "ayla_proposed"],
        ids=["none", "unknown_legacy", "missing-key", "ayla_proposed"],
    )
    def test_not_configured_sources_null_every_target(self, source: str) -> None:
        p = _profile(source)

        assert p.targets_state == TARGETS_NOT_CONFIGURED
        assert not p.targets_are_configured
        assert (p.daily_kcal, p.protein_g, p.fat_g, p.carbs_g, p.water_ml, p.bmr) == (None,) * 6

    @pytest.mark.parametrize("source", ["ayla_calculated", "user_entered"])
    def test_configured_sources_keep_them(self, source: str) -> None:
        """Положительная стража: инвариант не «обнуляет всё всегда»."""
        p = _profile(source)

        assert p.targets_state == TARGETS_CONFIGURED
        assert p.targets_are_configured
        assert (p.daily_kcal, p.protein_g, p.water_ml) == (KCAL, PROTEIN, WATER)

    def test_the_predicate_is_the_single_source_of_truth(self) -> None:
        """Свойства DTO — производные от одного предиката, не второй список."""
        assert targets_configured("user_entered")
        assert not targets_configured("unknown_legacy")
        assert not targets_configured(None)


class TestNoSurfacePrintsAnUnexplainedNumber:
    """Одни и те же три числа — ни одного при unknown_legacy, все при
    ayla_calculated. Различает только происхождение."""

    def test_unknown_legacy_prints_none_of_them(self) -> None:
        text = render_daily_report(_summary(), _water(), _profile("unknown_legacy"))

        # Положительная стража впереди отрицаний: факт напечатан — отчёт
        # не пустой, снимается ориентир, а не запись.
        assert "Калории: 640 ккал." in text
        assert "Вода: 700 мл." in text
        leaked = _numbers(text) & {PROTEIN, KCAL, WATER}
        assert not leaked, f"числа без происхождения дошли до человека: {leaked}"
        # ``calories_goal`` сводки — тоже: он без происхождения, а профиль
        # не настроен, значит сводке не верим.
        assert " из " not in text

    def test_ayla_calculated_prints_all_of_them(self) -> None:
        """Стража к предыдущему: рендер печатает ориентиры, когда можно."""
        text = render_daily_report(_summary(), _water(), _profile("ayla_calculated"))

        assert {PROTEIN, KCAL, WATER} <= _numbers(text)

    def test_no_assessment_without_configured_targets(self) -> None:
        """§6: до настройки скрыты оценки «мало / много / перебор» и
        «осталось». Все четыре реплики ``goal_remark`` — такие оценки."""
        assert goal_remark(_summary(), _water(), _profile("unknown_legacy")) == ""
        assert goal_remark(_summary(), _water(), _profile("none")) == ""

    def test_an_assessment_appears_once_targets_are_configured(self) -> None:
        """Стража: тот же вход, настроенный источник — реплика есть."""
        remark = goal_remark(_summary(), _water(), _profile("ayla_calculated"))

        assert remark, "при настроенных ориентирах оценка обязана появиться"
        assert "нормы" in remark
