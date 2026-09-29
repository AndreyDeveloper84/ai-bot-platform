"""DRF-2222 — подавление реплик читает то, что Ayla присылает СЕГОДНЯ.

Механизм, названный ревьюером #1950: пересчёт (#525, DRF-2192) кладёт новое
предложение в ``targets_provenance.pending_proposal`` рядом с действующим, и
если у предложения чувствительное переопределение, а у действующего нет,
``remarks_suppressed`` (читает только верхний ``goal_overridden_by``) реплику
пропускает.

Замер по коду Ayla ``origin/dev`` (21.09), ``nutrition/services``:

* ``compute_norms``: беременность / кормление / РПП — ОТКАЗ расчёта (с #372,
  DRF-1623 N-g), ``goal_overridden_by=""``, причина только в
  ``overrides_applied`` (``health_factor_*``); флаги остаются в
  ``health_flags`` (``pregnant`` / ``breastfeeding`` / ``eating_disorder``).
  Отказ в ``pending_proposal`` не попадает (``calculated_kept`` пуст при
  ``computed=False``) — так что в заявленном виде механизм НЕ воспроизводится;
* единственное переопределение, которое переживает расчёт и потому может
  лечь в предложение, — ``"bmr_floor"`` (lose → maintain). Бот ищет
  ``"bmi_floor"``: этого имени Ayla не выдавала никогда (DRF-300 → dev);
* ``"pregnancy"`` / ``"breastfeeding"`` / ``"eating_disorder"`` в
  ``goal_overridden_by`` Ayla больше не выдаёт. Из ``SENSITIVE_OVERRIDES``
  бота не совпадает ни одно значение — подавление держится только на
  ``health_flags["eating_disorder"]`` и на ``profile is None``;
* ручной ориентир (``user_entered``) при отказе остаётся действующим — реплики
  по нему идут, и беременность их не гасит.

p1–p3 — красные на замере (eb7cad4f), зелёные после правки; контроль в обе
стороны — p4; p5 — сторож дрейфа имён между каталогом и ботом.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from apps.integrations.ayla import ProfileResponse
from apps.nutrition_proactive.render import goal_remark, remarks_suppressed
from apps.nutrition_proactive.tests.test_render import summary as render_summary

_BASE = ProfileResponse(
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
    targets_source="ayla_calculated",
)

#: Сводка «ориентир ЕСТЬ» — из фикстур ``test_render.py`` (в списке
#: ``nutrition_target_guard``: изображать состояние «ориентир есть» можно
#: только там). Перебор при цели «снизить» — реплика есть у обычного профиля.
_SUMMARY = render_summary(calories_total=2400.0, protein_g=100.0)


def profile(**overrides: Any) -> ProfileResponse:
    return replace(_BASE, **overrides)


def with_pending(goal_overridden_by: str | None, **overrides: Any) -> ProfileResponse:
    """Действующий расчётный ориентир и предложение рядом — форма ответа Ayla
    ``_pending_block`` (profile_upsert_service, origin/dev)."""
    raw = {
        "targets_provenance": {
            "source": "ayla_calculated",
            "pending_proposal": {
                "kinds": ["calories"],
                "daily_kcal": 1750,
                "daily_protein_g": 90,
                "daily_fat_g": 58,
                "daily_carbs_g": 190,
                "input_snapshot": {"weight_kg": 58},
                "method_versions": {"calories": "mifflin_st_jeor_v1"},
                "goal": "maintain" if goal_overridden_by else "lose",
                "pace": "gentle",
                "goal_overridden_by": goal_overridden_by,
                "overrides_applied": (
                    [{"reason": goal_overridden_by}] if goal_overridden_by else []
                ),
            },
        }
    }
    return profile(raw=raw, **overrides)


# ── p1 — механизм ревьюера в той форме, в какой он возможен сегодня ──


class TestP1PendingOverride:
    def test_pending_bmr_floor_suppresses_while_current_has_none(self) -> None:
        p = with_pending("bmr_floor")
        assert p.goal_overridden_by is None  # положительно: у действующего переопределения нет
        assert remarks_suppressed(p) is True

    def test_and_the_report_carries_no_remark(self) -> None:
        assert goal_remark(_SUMMARY, None, with_pending("bmr_floor")) == ""


# ── p2 — имя переопределения, которое Ayla выдаёт ────────────────────


class TestP2OverrideNameAylaEmits:
    def test_current_bmr_floor_suppresses(self) -> None:
        assert remarks_suppressed(profile(goal_overridden_by="bmr_floor")) is True


# ── p3 — health-факторы после отказа: ручной ориентир остаётся ───────


class TestP3HealthFactorWithManualTarget:
    @pytest.mark.parametrize("flag", ["pregnant", "breastfeeding"])
    def test_flag_suppresses(self, flag: str) -> None:
        p = profile(
            health_flags={flag: True},
            targets_source="user_entered",
            calories_source="user_entered",
            goal_overridden_by=None,  # как присылает Ayla при отказе по health-фактору
        )
        assert p.calories_are_configured is True  # положительно: ручной ориентир действует
        assert remarks_suppressed(p) is True
        assert goal_remark(_SUMMARY, None, p) == ""


# ── p4 — контроль в обе стороны ──────────────────────────────────────


class TestP4Controls:
    def test_plain_profile_still_gets_a_remark(self) -> None:
        assert remarks_suppressed(profile()) is False
        assert goal_remark(_SUMMARY, None, profile()) != ""

    def test_pending_without_override_does_not_suppress(self) -> None:
        assert remarks_suppressed(with_pending(None)) is False

    def test_eating_disorder_flag_still_suppresses(self) -> None:
        assert remarks_suppressed(profile(health_flags={"eating_disorder": True})) is True

    def test_no_profile_is_silence(self) -> None:
        assert remarks_suppressed(None) is True

    def test_non_sensitive_pending_reason_does_not_suppress(self) -> None:
        """``activity_normalised`` едет в ``overrides_applied``, но не в
        ``goal_overridden_by`` — предложение с ним реплику не гасит."""
        p = with_pending(None)
        p.raw["targets_provenance"]["pending_proposal"]["overrides_applied"] = [
            {"reason": "activity_normalised"}
        ]
        assert remarks_suppressed(p) is False


# ── p5 — сторож дрейфа имён между каталогом и ботом ──────────────────

#: Копия перечня имён, которые каталог Ayla ВЫДАЁТ в ``goal_overridden_by``
#: (beautygo_backend ``origin/dev`` 08c0b26a):
#: ``nutrition/services/nutrition_profile_service.py:464`` —
#: ``overridden_by = overridden_by or "bmr_floor"``; ``:375`` — ``""`` у
#: отказа. Новое имя в каталоге → сначала сюда, и тест скажет, знает ли его
#: подавление. ``bmi_floor`` в боте прожил с DRF-300 до DRF-2222 именно
#: потому, что такой копии не было.
AYLA_EMITTED_OVERRIDES: frozenset[str] = frozenset({"bmr_floor"})

#: ``HEALTH_FACTOR_FLAGS`` каталога — там же, ``:184``.
AYLA_HEALTH_FACTOR_FLAGS: frozenset[str] = frozenset(
    {"pregnant", "breastfeeding", "eating_disorder"}
)


class TestP5CatalogueDriftGuard:
    def test_every_override_ayla_emits_is_sensitive(self) -> None:
        from apps.nutrition_proactive.render import SENSITIVE_OVERRIDES

        assert AYLA_EMITTED_OVERRIDES  # положительно: перечень не пуст
        assert AYLA_EMITTED_OVERRIDES <= SENSITIVE_OVERRIDES, (
            AYLA_EMITTED_OVERRIDES - SENSITIVE_OVERRIDES
        )

    def test_every_health_factor_ayla_refuses_on_is_read(self) -> None:
        from apps.nutrition_proactive.render import SENSITIVE_HEALTH_FLAGS

        assert AYLA_HEALTH_FACTOR_FLAGS == SENSITIVE_HEALTH_FLAGS
