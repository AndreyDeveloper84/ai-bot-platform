"""What the two proactive messages actually say (DRF-1285).

Kept in its own module so the boundary below can be read and audited on one
screen, without scrolling past selection logic and Celery plumbing.

### The boundary

Owner rule, 2026-08-23: **we may talk about the person's data; we may not
talk about the person's body.**

Allowed:

* Arithmetic over numbers *they* logged and norms from *their own* profile.
  "Белки: 60 из 95 г" is their entry against their profile's target.
* A light remark tied to the goal **they themselves named** in the anketa
  (``ProfileResponse.goal`` -- lose / maintain / gain / tone). Suggesting
  what to top up first, given the goal on file, is help.

Never:

* A diagnosis, a symptom reading, a claim about how they feel, a nutrient
  "deficiency", advice to take anything, or any sentence about health as
  such. The bot does not know, and saying it anyway is the whole risk.
* A number the person did not produce and the profile does not contain.
  Every figure printed here traces to ``summary`` (what they logged),
  ``water`` (what they logged), or ``profile`` (norms Ayla computed from
  the anketa they filled in).
* Any remark at all when the profile carries a
  :data:`SENSITIVE_OVERRIDES` marker -- see below.

### Why the remark goes silent for some people

``ProfileResponse.goal_overridden_by`` is Ayla's own signal that it
overrode the stated goal because of pregnancy, breastfeeding, an eating
disorder, or a BMI floor; ``health_flags["eating_disorder"]`` is the
explicit flag the legacy bot also honoured. For those people the numbers
still render -- they asked for their diary back -- but every trace of
"you are behind on X, top it up" is dropped. A nudge toward eating more or
less is exactly the sentence that stops being neutral in that context, and
an unprompted evening message is the worst possible place to get it wrong.
"""

from __future__ import annotations

from typing import Any

from apps.integrations.ayla import ProfileResponse, SummaryResponse, WaterTodayResponse

#: ``goal_overridden_by`` values that suppress every remark. ``bmi_floor``
#: is included: Ayla raises the floor when a loss target would go too low,
#: and "you ate under your calories" is not a neutral observation there.
SENSITIVE_OVERRIDES: frozenset[str] = frozenset(
    {"pregnancy", "breastfeeding", "eating_disorder", "bmi_floor"}
)

#: A macro under this share of its profile norm is what the remark points
#: at. 0.7 rather than a tighter band because the remark should fire on a
#: clear shortfall, not on a rounding difference -- one sentence a day that
#: is obviously right beats a daily sentence that is arguably right.
SHORTFALL_RATIO = 0.7

#: Over this share of the calorie goal counts as "over the number on file".
OVERSHOOT_RATIO = 1.1

OPT_OUT_HINT = "Если такие итоги не нужны — напиши «не пиши мне»."
WATER_OPT_OUT_HINT = "Если напоминания не нужны — напиши «не пиши мне»."

#: The goals the anketa offers, in the words the person picked. Used only to
#: quote their own choice back -- never to infer anything from it.
GOAL_LABELS: dict[str, str] = {
    "lose": "снизить вес",
    "gain": "набрать вес",
    "maintain": "удержать вес",
    "tone": "подтянуть форму",
}


def remarks_suppressed(profile: ProfileResponse | None) -> bool:
    """True when this person gets numbers only, never a suggestion."""
    if profile is None:
        return True
    if str(profile.goal_overridden_by or "") in SENSITIVE_OVERRIDES:
        return True
    flags: dict[str, Any] = profile.health_flags or {}
    return bool(flags.get("eating_disorder"))


def render_daily_report(
    summary: SummaryResponse,
    water: WaterTodayResponse | None,
    profile: ProfileResponse | None = None,
) -> str:
    """Compose the daily report.

    Structure: what was logged against what the profile expects, then at
    most **one** remark, then Ayla's own comment if it sent one, then the
    off-switch. Degrades cleanly -- without a profile the macro targets are
    simply absent and no remark is made, so an Ayla outage costs detail
    rather than correctness.
    """
    lines: list[str] = ["Итоги дня по питанию."]

    if not _anything_logged(summary, water):
        # Silence beats a table of zeros. A report reading "0 из 1900" is a
        # scoreboard of a day the person chose not to log, and the bot has
        # no business scoring that.
        lines.append("Сегодня записей не было — считать нечего.")
        lines.append(OPT_OUT_HINT)
        return "\n".join(lines)

    lines.append("")
    lines.append(_macro_line("Калории", summary.calories_total, summary.calories_goal, "ккал"))
    lines.append(_macro_line("Белки", summary.protein_g, _target(profile, "protein_g"), "г"))
    lines.append(_macro_line("Жиры", summary.fat_g, _target(profile, "fat_g"), "г"))
    lines.append(_macro_line("Углеводы", summary.carbs_g, _target(profile, "carbs_g"), "г"))
    if water is not None and water.norm_ml:
        lines.append(_macro_line("Вода", water.total_ml, water.norm_ml, "мл"))

    remark = goal_remark(summary, water, profile)
    if remark:
        lines.append("")
        lines.append(remark)

    if summary.ai_comment:
        # Ayla's own text, generated under Ayla's safety rules. Passed
        # through verbatim rather than paraphrased -- rewording someone
        # else's reviewed copy is how a reviewed sentence stops being one.
        lines.append("")
        lines.append(summary.ai_comment)

    lines.append("")
    lines.append(OPT_OUT_HINT)
    return "\n".join(lines)


def goal_remark(
    summary: SummaryResponse,
    water: WaterTodayResponse | None,
    profile: ProfileResponse | None,
) -> str:
    """At most one sentence, tied to the goal the person named. May be "".

    Ordered by priority, first match wins. One remark, not a list: a daily
    message that itemises three shortfalls is a daily message that gets
    muted. Every rule only compares a logged number against a profile
    number and, at most, quotes the goal already on file.
    """
    if remarks_suppressed(profile):
        return ""
    assert profile is not None  # narrowed by remarks_suppressed

    goal_label = GOAL_LABELS.get(profile.goal, "")

    if profile.protein_g and summary.protein_g < profile.protein_g * SHORTFALL_RATIO:
        short = round(profile.protein_g - summary.protein_g)
        tail = f" — при цели «{goal_label}» его обычно добирают первым" if goal_label else ""
        return f"Белка сегодня меньше нормы из профиля на {short} г{tail}."

    if water is not None and water.norm_ml and water.total_ml < water.norm_ml * SHORTFALL_RATIO:
        return f"До нормы воды из профиля осталось {water.norm_ml - water.total_ml} мл."

    if (
        profile.goal in {"lose", "tone"}
        and summary.calories_goal
        and summary.calories_total > summary.calories_goal * OVERSHOOT_RATIO
    ):
        over = round(summary.calories_total - summary.calories_goal)
        tail = f" — цель в профиле «{goal_label}»" if goal_label else ""
        return f"Калорий вышло на {over} ккал больше нормы из профиля{tail}."

    if summary.calories_goal and summary.calories_total >= summary.calories_goal * SHORTFALL_RATIO:
        return "День уложился в нормы из твоего профиля."

    return ""


def render_water_reminder(
    water: WaterTodayResponse,
    proportional_ml: int | None = None,
) -> str:
    """Compose the water nudge.

    Two numbers and, when the caller supplies it, the third that explains
    why the bot spoke at this hour rather than any other: the share of the
    norm that has come due so far. Naming it turns "you are behind" into
    "here is the arithmetic" -- and it is the same figure the gate used, so
    the message cannot disagree with the decision that produced it.
    """
    deficit = max(0, water.norm_ml - water.total_ml)
    lines = [f"Сегодня выпито {water.total_ml} из {water.norm_ml} мл."]
    if proportional_ml:
        lines.append(f"К этому часу по профилю — около {proportional_ml} мл.")
    lines.append(f"До дневной нормы ещё {deficit} мл.")
    lines.append(WATER_OPT_OUT_HINT)
    return "\n".join(lines)


# -- helpers ----------------------------------------------------------------


def _anything_logged(summary: SummaryResponse, water: WaterTodayResponse | None) -> bool:
    logged_food = summary.calories_total > 0 or bool(summary.entries)
    logged_water = water is not None and water.total_ml > 0
    return logged_food or logged_water


def _target(profile: ProfileResponse | None, field: str) -> float:
    return float(getattr(profile, field, 0) or 0) if profile is not None else 0.0


def _macro_line(label: str, actual: float, target: float, unit: str) -> str:
    """``Белки: 80 из 95 г`` -- or without the target when none is known."""
    if target:
        return f"{label}: {round(actual)} из {round(target)} {unit}."
    return f"{label}: {round(actual)} {unit}."
