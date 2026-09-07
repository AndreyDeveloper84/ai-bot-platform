"""What the coach hint says — all of it, on one screen (DRF-1464, T5).

Owner decision Q-07: the proactive hint is TEMPLATED per (goal ×
trigger) pair — no model writes proactive copy. This file is every
sentence the coach_hint surface can emit, kept apart from the counting
(:mod:`apps.nutrition_coach.triggers`) so the editorial boundary can be
read and audited without scrolling past mechanics.

### The rules each line was written against

(``docs/design/policies/nutrition-coach-copy-policy.md`` — the
enforceable versions are tested in ``tests/test_copy.py``.)

* **R1 — outcome, not habit.** Each line points at how the person wants
  to feel (спать, энергия) and offers to look at their records together.
  None prescribes a routine: «ешь лёгкое», «завтракай» would not pass.
* **R2/R3 — no reproach, no streaks.** Nothing is counted in the text
  (Q-10: the trigger counts, the text never names days or hours), and
  nothing frames an absence: the breakfast line exists because breakfasts
  ARE in the diary, and says exactly that.
* **R4 — no scales, sizes, deficits.** No kilograms, calories or
  «минус» anywhere below.
* **R6 — autonomy.** The first hint a person ever receives carries
  :data:`FIRST_HINT_TAIL` — cadence and the off-switch, in the owner's
  exact wording. The one-tap unsubscribe button itself is attached by
  the delivery path (``tasks._stop_keyboard``), not by the text.
* **R7 — onto an explicit goal.** A template is chosen by trigger kind,
  and a trigger exists only for a curated goal key — so no line here can
  reach a person who never chose a goal.

Review question for any edit, from the policy: «если человек прочитает
это в плохой день — это звучит как поддержка или как упрёк?»
"""

from __future__ import annotations

from typing import Final

#: One template per trigger kind. The kind already implies the goal
#: family (:mod:`apps.nutrition_coach.triggers` fires ``late_dinner``
#: only for sleep goals and ``breakfasts`` only for energy goals), so
#: keying by kind IS keying by the (goal × trigger) pair.
HINT_TEXTS: Final[dict[str, str]] = {
    # Поздний ужин × цель сна. «Поздний» остаётся в механике (Q-10):
    # человек читает про свой исход — сон — и про совместный разбор.
    "late_dinner": (
        "Ты говорила, что хочешь лучше спать. "
        "Могу посмотреть, как ужины на этой неделе в твоих записях "
        "связаны со сном, — скажи, если интересно."
    ),
    # Завтраки × цель энергии. Через присутствие, не отсутствие:
    # «в записях есть завтраки», никакого «пропуска» (R3).
    "breakfasts": (
        "Ты говорила, что хочешь больше энергии днём. "
        "На этой неделе в записях есть завтраки — если хочешь, "
        "посмотрим, какие из них совпадают с бодрыми днями."
    ),
}

#: R6, owner wording verbatim: the first hint in a person's life says
#: how often these come and where the off-switch is. Appended once,
#: never repeated — a weekly reminder of the off-switch is itself nag.
FIRST_HINT_TAIL: Final[str] = "Присылаю такое не чаще раза в неделю. Не нужно — кнопка ниже."


def render_hint(kind: str, *, first_ever: bool) -> str:
    """The hint text for a fired trigger kind.

    ``KeyError`` on an unknown kind is deliberate: a trigger without a
    template is a hint that must not go out, and failing loud inside
    the planner's evaluation (which turns it into a logged skip) beats
    improvising a sentence.
    """
    text = HINT_TEXTS[kind]
    if first_ever:
        return f"{text}\n\n{FIRST_HINT_TAIL}"
    return text


__all__ = ["FIRST_HINT_TAIL", "HINT_TEXTS", "render_hint"]
