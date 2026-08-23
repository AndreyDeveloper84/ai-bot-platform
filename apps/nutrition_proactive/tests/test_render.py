"""The content boundary, enforced (DRF-1285).

Owner rule, 2026-08-23: we may talk about the person's data, not about the
person's body. These tests are the executable form of that sentence — the
positive half (the report is substantive, the remark is useful) and the
negative half (no number the person did not produce, no claim about health,
and total silence on suggestions for the people Ayla has flagged).

Pure functions, no database.
"""

from __future__ import annotations

import pytest

from apps.integrations.ayla import ProfileResponse, SummaryResponse, WaterTodayResponse
from apps.nutrition_proactive import render


def profile(**overrides) -> ProfileResponse:
    base = dict(
        gender="female",
        age=32,
        height_cm=168,
        weight_kg=64,
        goal="lose",
        daily_kcal=1900,
        protein_g=95,
        fat_g=60,
        carbs_g=210,
        water_ml=2000,
        bmr=1400,
        health_flags={},
        disclaimer_acked=None,
    )
    base.update(overrides)
    return ProfileResponse(**base)


def summary(**overrides) -> SummaryResponse:
    base = dict(
        date="2026-08-23",
        calories_total=1500.0,
        calories_goal=1900,
        protein_g=80.0,
        fat_g=55.0,
        carbs_g=160.0,
        entries=[{"id": 1}],
        raw={},
    )
    base.update(overrides)
    return SummaryResponse(**base)


def water(total_ml: int = 1600, norm_ml: int = 2000) -> WaterTodayResponse:
    return WaterTodayResponse(total_ml=total_ml, norm_ml=norm_ml, entries=[])


# ---------------------------------------------------------------------------
# The positive half — the report is worth receiving
# ---------------------------------------------------------------------------


class TestReportIsSubstantive:
    def test_every_macro_is_shown_against_its_profile_target(self) -> None:
        text = render.render_daily_report(summary(), water(), profile())
        assert "Калории: 1500 из 1900 ккал." in text
        assert "Белки: 80 из 95 г." in text
        assert "Жиры: 55 из 60 г." in text
        assert "Углеводы: 160 из 210 г." in text
        assert "Вода: 1600 из 2000 мл." in text

    def test_a_clear_protein_shortfall_earns_a_remark(self) -> None:
        text = render.render_daily_report(summary(protein_g=40.0), water(), profile())
        assert "Белка сегодня меньше нормы из профиля на 55 г" in text
        assert "снизить вес" in text

    def test_the_remark_quotes_the_goal_the_person_chose(self) -> None:
        for goal, label in render.GOAL_LABELS.items():
            remark = render.goal_remark(summary(protein_g=10.0), water(), profile(goal=goal))
            assert label in remark

    def test_an_overshoot_is_stated_as_arithmetic(self) -> None:
        remark = render.goal_remark(summary(calories_total=2300.0), water(), profile(goal="lose"))
        assert remark == (
            "Калорий вышло на 400 ккал больше нормы из профиля — цель в профиле «снизить вес»."
        )

    def test_a_day_within_the_bands_is_acknowledged(self) -> None:
        remark = render.goal_remark(summary(), water(), profile())
        assert remark == "День уложился в нормы из твоего профиля."

    def test_at_most_one_remark_ever(self) -> None:
        """Protein short AND water short AND over calories — still one line."""
        text = render.render_daily_report(
            summary(protein_g=10.0, calories_total=3000.0),
            water(total_ml=100),
            profile(),
        )
        remarks = [
            line
            for line in text.splitlines()
            if line
            and not line.startswith(
                ("Итоги", "Калории", "Белки", "Жиры", "Углеводы", "Вода", "Если")
            )
        ]
        assert len(remarks) == 1

    def test_ayla_own_comment_is_passed_through_verbatim(self) -> None:
        comment = "Сегодня в рационе много овощей."
        text = render.render_daily_report(summary(ai_comment=comment), water(), profile())
        assert comment in text


class TestWaterReminderExplainsItself:
    def test_it_names_the_share_due_by_now(self) -> None:
        text = render.render_water_reminder(water(total_ml=100), proportional_ml=375)
        assert "Сегодня выпито 100 из 2000 мл." in text
        assert "К этому часу по профилю — около 375 мл." in text
        assert "До дневной нормы ещё 1900 мл." in text

    def test_it_still_reads_without_the_proportional_figure(self) -> None:
        text = render.render_water_reminder(water(total_ml=100))
        assert "К этому часу" not in text
        assert "Сегодня выпито 100 из 2000 мл." in text


# ---------------------------------------------------------------------------
# The negative half — what must never appear
# ---------------------------------------------------------------------------


#: Words that would move a sentence from "your data" to "your body". Not an
#: exhaustive filter — a guard against the drift that happens when copy is
#: edited later by someone who has not read the boundary.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "дефицит",
    "нехватка",
    "здоров",
    "болезн",
    "симптом",
    "диагноз",
    "лечен",
    "витамин",
    "добавк",
    "препарат",
    "врач",
    "анализ крови",
    "обмен веществ",
    "организм",
    "самочувств",
    "усталост",
    "иммунитет",
)


class TestNeverTalksAboutTheBody:
    @pytest.mark.parametrize(
        "case",
        [
            (summary(), water(), profile()),
            (summary(protein_g=5.0), water(total_ml=50), profile(goal="gain")),
            (summary(calories_total=4000.0), water(), profile(goal="lose")),
            (summary(calories_total=0.0, entries=[]), None, profile()),
            (summary(), water(), None),
        ],
    )
    def test_no_report_mentions_the_body(self, case) -> None:
        text = render.render_daily_report(*case).lower()
        hits = [w for w in FORBIDDEN_SUBSTRINGS if w in text]
        assert hits == []

    def test_the_water_reminder_does_not_either(self) -> None:
        text = render.render_water_reminder(water(total_ml=0), proportional_ml=375).lower()
        assert [w for w in FORBIDDEN_SUBSTRINGS if w in text] == []

    def test_every_number_printed_comes_from_the_inputs(self) -> None:
        """No figure appears that is not a logged value, a profile norm, or
        arithmetic over the two. The guard is exact: pull every integer out
        of the rendered text and account for each one."""
        import re

        s, w, p = summary(protein_g=40.0), water(total_ml=1600), profile()
        text = render.render_daily_report(s, w, p)
        printed = {int(n) for n in re.findall(r"\d+", text)}
        allowed = {
            round(s.calories_total),
            s.calories_goal,
            round(s.protein_g),
            p.protein_g,
            round(s.fat_g),
            p.fat_g,
            round(s.carbs_g),
            p.carbs_g,
            w.total_ml,
            w.norm_ml,
            round(p.protein_g - s.protein_g),  # the shortfall in the remark
        }
        assert printed <= allowed, f"unexplained numbers: {printed - allowed}"


class TestSuppressedForFlaggedProfiles:
    @pytest.mark.parametrize("override", sorted(render.SENSITIVE_OVERRIDES))
    def test_no_remark_when_ayla_overrode_the_goal(self, override: str) -> None:
        p = profile(goal_overridden_by=override)
        assert render.remarks_suppressed(p) is True
        assert render.goal_remark(summary(protein_g=5.0), water(total_ml=0), p) == ""

    def test_no_remark_on_the_eating_disorder_flag(self) -> None:
        p = profile(health_flags={"eating_disorder": True})
        assert render.remarks_suppressed(p) is True
        assert render.goal_remark(summary(protein_g=5.0), water(total_ml=0), p) == ""

    def test_the_numbers_still_render(self) -> None:
        """They asked for their diary back; they get their diary back. What
        is withheld is the nudge, not the data."""
        p = profile(health_flags={"eating_disorder": True})
        text = render.render_daily_report(summary(protein_g=5.0), water(), p)
        assert "Калории: 1500 из 1900 ккал." in text
        assert "Белка сегодня меньше" not in text
        assert "уложился" not in text

    def test_a_missing_profile_suppresses_too(self) -> None:
        """No profile means no norms, and a remark without norms would be an
        opinion rather than arithmetic."""
        assert render.remarks_suppressed(None) is True
        text = render.render_daily_report(summary(), water(), None)
        assert "Белки: 80 г." in text  # no target shown
        assert render.goal_remark(summary(), water(), None) == ""


class TestEmptyDay:
    def test_nothing_logged_is_not_a_scoreboard_of_zeros(self) -> None:
        text = render.render_daily_report(
            summary(calories_total=0.0, entries=[]), water(total_ml=0), profile()
        )
        assert "Сегодня записей не было" in text
        assert "0 из 1900" not in text
        assert "Белки" not in text

    def test_water_alone_still_counts_as_a_logged_day(self) -> None:
        text = render.render_daily_report(
            summary(calories_total=0.0, entries=[]), water(total_ml=500), profile()
        )
        assert "Сегодня записей не было" not in text
        assert "Вода: 500 из 2000 мл." in text
