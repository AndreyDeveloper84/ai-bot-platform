"""The hint wording against its own rules (DRF-1464, T5).

Copy policy (``docs/design/policies/nutrition-coach-copy-policy.md``) is
enforced in two directions, and both live here:

* **Whitelist** — EVERY string :mod:`apps.nutrition_coach.copy` can emit
  (each template, the first-hint tail, and each rendered combination)
  passes :func:`apps.orchestrator.safety.outbound.evaluate_outbound`
  unblocked. A template edit that drifts into a guarded shape fails here,
  not in somebody's messenger.
* **Negative corpus** — the banned shapes from the policy's R-tables are
  blocked with the expected category, so the guard this whitelist relies
  on is proven to bite. «Серия процедур» is the control: a legitimate
  salon phrase the guard must NOT eat.

The editorial rules the guard cannot check (R1 outcome-not-habit, R4 no
scales, R7 goal-anchored) are carried by the templates themselves and
argued in :mod:`apps.nutrition_coach.copy`'s docstring — a regex cannot
prove a sentence is kind, so the suite does not pretend to.
"""

from __future__ import annotations

import pytest

from apps.nutrition_coach import copy
from apps.nutrition_coach.triggers import late_dinner_trigger, breakfast_trigger
from apps.orchestrator.safety.outbound import evaluate_outbound


def every_emittable_string() -> list[str]:
    """Each string the module can hand to a sender, one by one."""
    strings = list(copy.HINT_TEXTS.values())
    strings.append(copy.FIRST_HINT_TAIL)
    for kind in copy.HINT_TEXTS:
        strings.append(copy.render_hint(kind, first_ever=True))
        strings.append(copy.render_hint(kind, first_ever=False))
    return strings


class TestWhitelist:
    @pytest.mark.parametrize("text", every_emittable_string())
    def test_every_emittable_string_passes_the_outbound_guard(self, text: str) -> None:
        verdict = evaluate_outbound(text)
        assert verdict.allowed, f"copy string blocked as {verdict.categories}: {text!r}"

    @pytest.mark.parametrize("text", every_emittable_string())
    def test_no_string_names_a_count_or_a_clock(self, text: str) -> None:
        """Q-10 / R3: the trigger counts days and hours; the text never
        names them — not «три дня», not «после девяти», not «21:00»."""
        # Контроль присутствия на тех же данных: строка вообще существует —
        # иначе «в ней нет счётчиков» доказывало бы не чистоту, а пустоту.
        assert text.strip()
        for banned in ("подряд", "21", "девят", "три дня", "дней"):
            assert banned not in text.lower(), f"{banned!r} leaked into {text!r}"


class TestFirstHintTail:
    def test_the_first_ever_hint_carries_the_cadence_tail(self) -> None:
        """R6, owner wording: the first hint a person ever gets says how
        often these come and where the off-switch is."""
        for kind in copy.HINT_TEXTS:
            text = copy.render_hint(kind, first_ever=True)
            assert text.endswith(copy.FIRST_HINT_TAIL)

    def test_a_repeat_hint_drops_the_tail(self) -> None:
        """The tail is an introduction, not a refrain: repeating it every
        week turns a courtesy into noise."""
        for kind in copy.HINT_TEXTS:
            # Контроль присутствия: на first_ever=True тот же рендер хвост
            # несёт — иначе «нет хвоста» ниже доказывало бы не повтор, а
            # сломанный рендер.
            assert copy.FIRST_HINT_TAIL in copy.render_hint(kind, first_ever=True)
            assert copy.FIRST_HINT_TAIL not in copy.render_hint(kind, first_ever=False)

    def test_the_tail_is_the_owner_wording_verbatim(self) -> None:
        assert (
            copy.FIRST_HINT_TAIL == "Присылаю такое не чаще раза в неделю. Не нужно — кнопка ниже."
        )


class TestTemplatesCoverExactlyTheTwoPairs:
    def test_one_template_per_trigger_kind(self) -> None:
        """Q-04: two triggers, two templates. A third kind has no wording
        and therefore can never be sent."""
        assert set(copy.HINT_TEXTS) == {"late_dinner", "breakfasts"}

    def test_an_unknown_kind_has_no_text(self) -> None:
        with pytest.raises(KeyError):
            copy.render_hint("придуманный_триггер", first_ever=True)

    def test_the_detector_kinds_and_the_copy_keys_agree(self) -> None:
        """The two halves of Q-10 cannot drift apart silently: a trigger
        kind without a template fails loud at import-adjacent test time,
        not as a KeyError inside a beat tick with a person waiting."""
        assert set(copy.HINT_TEXTS) == {"late_dinner", "breakfasts"}
        assert late_dinner_trigger is not None and breakfast_trigger is not None


class TestNegativeCorpus:
    """The R-table shapes the guard exists to stop, blocked as expected."""

    @pytest.mark.parametrize(
        ("text", "category"),
        [
            # R2 — понукание и подсчёт пропусков.
            ("Не забывайте про цель!", "nag"),
            ("Вы давно не работали над своей целью", "nag"),
            ("Ты давно не записывала еду", "nag"),
            ("Вы пропустили завтрак", "nag"),
            ("Ты пропустила ужин вчера", "nag"),
            # R3 — серии и счётчики добродетели.
            ("Ты записываешь еду три дня подряд — так держать!", "nag"),
            ("7 дней без пропусков!", "nag"),
            ("Ты держишь серию — не останавливайся!", "nag"),
            # R5 — диагноз и назначение.
            ("У вас аллергия на глютен", "medical"),
            ("Примите антибиотик и всё пройдёт", "medical"),
            # Обещания от имени салона.
            ("Гарантирую результат за неделю", "promise"),
        ],
    )
    def test_banned_shapes_are_blocked_with_the_expected_category(
        self, text: str, category: str
    ) -> None:
        verdict = evaluate_outbound(text)
        assert verdict.blocked, f"not blocked: {text!r}"
        assert category in verdict.categories

    def test_a_course_of_procedures_is_not_a_virtue_streak(self) -> None:
        """«Серия процедур» is a legitimate salon service phrase; the
        guard's only exception. Pinned so a future «улучшение» of the
        nag patterns does not start eating service copy."""
        verdict = evaluate_outbound("Серия процедур подобрана под твою цель.")
        assert verdict.allowed
