"""Service-layer tests for food_scanner Веха 2.

Covers:

* ``is_ed_mode_active`` — OR of the two profile signals.
* ``redact_scan_for_ed`` / ``redact_log_for_ed`` / ``redact_summary_for_ed``
  — numeric fields nulled in ED-mode, neutral fields preserved.
* ``serialize_health_flags`` — wire payload for ``/customer/health-flags``.
"""

from __future__ import annotations

from apps.integrations.ayla.nutrition_client import (
    FoodLogResponse,
    ProfileResponse,
    ScanResponse,
    SummaryResponse,
)
from apps.skills.food_scanner.services import (
    is_ed_mode_active,
    redact_log_for_ed,
    redact_scan_for_ed,
    redact_summary_for_ed,
    serialize_health_flags,
)


def _profile(
    *, goal_overridden_by: str | None = None, flags: dict | None = None
) -> ProfileResponse:
    return ProfileResponse(
        gender="female",
        age=30,
        height_cm=170,
        weight_kg=60,
        goal="maintain",
        daily_kcal=2000,
        protein_g=100,
        fat_g=70,
        carbs_g=250,
        water_ml=2000,
        bmr=1400,
        health_flags=flags or {},
        disclaimer_acked=None,
        goal_overridden_by=goal_overridden_by,
    )


def _scan() -> ScanResponse:
    return ScanResponse(
        scan_id="scan-1",
        dish_name="Борщ",
        confidence=0.86,
        portion_g=320,
        nutrition={"calories": 250, "protein_g": 12, "fat_g": 8, "carbs_g": 32},
        provider="openai-gpt-4o",
        raw={},
    )


def _log() -> FoodLogResponse:
    return FoodLogResponse(
        log_id="log-1",
        dish_name="Борщ",
        meal_type="lunch",
        calories=250.0,
        raw={},
    )


def _summary(*, ai_comment: str | None = "Хороший день — сегодня 1450 ккал.") -> SummaryResponse:
    return SummaryResponse(
        date="2026-06-02",
        calories_total=1240.0,
        calories_goal=2100,
        protein_g=65.4,
        fat_g=40.1,
        carbs_g=120.9,
        entries=[
            {
                "log_id": "log-1",
                "dish_name": "Овсянка",
                "meal_type": "breakfast",
                "logged_at": "2026-06-02T07:25:00Z",
                "nutrition": {"calories": 320, "protein_g": 9, "fat_g": 7, "carbs_g": 56},
            },
        ],
        raw={},
        ai_comment=ai_comment,
    )


class TestEdModeDetection:
    def test_none_profile_returns_false(self):
        assert is_ed_mode_active(None) is False

    def test_no_flags_returns_false(self):
        assert is_ed_mode_active(_profile()) is False

    def test_goal_override_activates(self):
        assert is_ed_mode_active(_profile(goal_overridden_by="eating_disorder")) is True

    def test_health_flag_activates(self):
        assert is_ed_mode_active(_profile(flags={"eating_disorder": True})) is True

    def test_both_activate(self):
        p = _profile(goal_overridden_by="eating_disorder", flags={"eating_disorder": True})
        assert is_ed_mode_active(p) is True

    def test_other_override_does_not_activate(self):
        assert is_ed_mode_active(_profile(goal_overridden_by="pregnancy")) is False

    def test_health_flag_false_does_not_activate(self):
        assert is_ed_mode_active(_profile(flags={"eating_disorder": False})) is False


class TestRedactScan:
    def test_ed_off_preserves_nutrition(self):
        out = redact_scan_for_ed(_scan(), ed_mode=False)
        assert out["nutrition"] == {
            "calories": 250,
            "protein_g": 12,
            "fat_g": 8,
            "carbs_g": 32,
        }
        assert out["ed_mode"] is False
        assert out["dish_name"] == "Борщ"
        assert out["portion_g"] == 320

    def test_ed_on_nulls_nutrition_preserves_neutral_fields(self):
        out = redact_scan_for_ed(_scan(), ed_mode=True)
        assert out["nutrition"] is None
        assert out["ed_mode"] is True
        # Neutral fields preserved.
        assert out["dish_name"] == "Борщ"
        assert out["portion_g"] == 320
        assert out["confidence"] == 0.86
        assert out["scan_id"] == "scan-1"
        assert out["provider"] == "openai-gpt-4o"

    def test_nutrition_allowlist_drops_unknown_keys(self):
        # Адверсариальный обзор PRE_PILOT #P2 — defence against Ayla
        # schema drift. Even non-ED responses must drop fields not on
        # the explicit allowlist so a new Ayla key cannot ship to the
        # wire without a contract-side review.
        scan = ScanResponse(
            scan_id="scan-1",
            dish_name="X",
            confidence=0.5,
            portion_g=100,
            nutrition={
                "calories": 200,
                "protein_g": 10,
                "fat_g": 5,
                "carbs_g": 20,
                "deficit_surplus": -500,  # new field — must be dropped
                "score": 7.2,  # new field — must be dropped
            },
            provider="x",
            raw={},
        )
        out = redact_scan_for_ed(scan, ed_mode=False)
        assert set(out["nutrition"].keys()) == {"calories", "protein_g", "fat_g", "carbs_g"}


class TestRedactLog:
    def test_ed_off_keeps_calories(self):
        out = redact_log_for_ed(_log(), ed_mode=False)
        assert out["calories"] == 250.0
        assert out["ed_mode"] is False

    def test_ed_on_nulls_calories(self):
        out = redact_log_for_ed(_log(), ed_mode=True)
        assert out["calories"] is None
        assert out["ed_mode"] is True
        assert out["dish_name"] == "Борщ"
        assert out["meal_type"] == "lunch"
        assert out["log_id"] == "log-1"


class TestRedactSummary:
    def test_ed_off_full_numbers(self):
        out = redact_summary_for_ed(_summary(), ed_mode=False)
        assert out["calories_total"] == 1240
        assert out["calories_goal"] == 2100
        assert out["protein_g"] == 65
        assert out["fat_g"] == 40
        assert out["carbs_g"] == 121
        assert out["entries"][0]["nutrition"] == {
            "calories": 320,
            "protein_g": 9,
            "fat_g": 7,
            "carbs_g": 56,
        }
        assert out["ed_mode"] is False

    def test_ed_on_nulls_numbers_preserves_entries(self):
        out = redact_summary_for_ed(_summary(), ed_mode=True)
        assert out["calories_total"] is None
        assert out["calories_goal"] is None
        assert out["protein_g"] is None
        assert out["fat_g"] is None
        assert out["carbs_g"] is None
        # Entry-level: dish name + timestamp + meal type preserved; nutrition stripped.
        e = out["entries"][0]
        assert e["dish_name"] == "Овсянка"
        assert e["meal_type"] == "breakfast"
        assert e["logged_at"] == "2026-06-02T07:25:00Z"
        assert e["nutrition"] is None
        assert out["ed_mode"] is True

    def test_ed_on_drops_ai_comment(self):
        # Адверсариальный обзор PRE_PILOT #P1 — Ayla's prose
        # ``ai_comment`` can embed numeric counts that would bypass
        # every other redaction. Drop the field entirely in ED-mode.
        out = redact_summary_for_ed(_summary(ai_comment="Сегодня 1450/1800 ккал."), ed_mode=True)
        assert out["ai_comment"] is None

    def test_ed_off_passes_ai_comment_through(self):
        out = redact_summary_for_ed(_summary(ai_comment="Хорошее утро."), ed_mode=False)
        assert out["ai_comment"] == "Хорошее утро."

    def test_empty_entries(self):
        s = _summary()
        s = SummaryResponse(
            date=s.date,
            calories_total=s.calories_total,
            calories_goal=s.calories_goal,
            protein_g=s.protein_g,
            fat_g=s.fat_g,
            carbs_g=s.carbs_g,
            entries=[],
            raw={},
        )
        out = redact_summary_for_ed(s, ed_mode=False)
        assert out["entries"] == []


class TestHealthFlagsPayload:
    def test_no_profile_returns_all_false(self):
        out = serialize_health_flags(None)
        assert out == {
            "eating_disorder": False,
            "pregnancy": False,
            "breastfeeding": False,
            "ed_mode": False,
        }

    def test_eating_disorder_via_health_flag(self):
        out = serialize_health_flags(_profile(flags={"eating_disorder": True}))
        assert out["eating_disorder"] is True
        assert out["ed_mode"] is True

    def test_eating_disorder_via_override(self):
        out = serialize_health_flags(_profile(goal_overridden_by="eating_disorder"))
        assert out["eating_disorder"] is True
        assert out["ed_mode"] is True

    def test_pregnancy_and_breastfeeding_passthrough(self):
        out = serialize_health_flags(_profile(flags={"pregnancy": True, "breastfeeding": True}))
        assert out == {
            "eating_disorder": False,
            "pregnancy": True,
            "breastfeeding": True,
            "ed_mode": False,
        }
