"""Nutrition anketa FSM — 7 steps, screening before anthropometry.

Walks the user through
``gender → age → screening → height → weight → activity → goal``, then
signals COMPLETE. The skill (``skill.py``) takes the completed answers, POSTs to
Ayla ``upsert_profile``, and renders the targets card.

## Why screening sits third, before height and weight

Owner decision of 2026-09-09 (``docs/decisions/
AYLA_NUTRITION_TARGETS_ARCHITECTURE_DECISION.md`` §7.1): Ayla does not
compute targets automatically for minors, during pregnancy or nursing,
for a declared eating disorder, or for a condition affecting nutrition
or fluid balance. Those people keep the diary; they just don't get a
computed number.

The order follows from that. Asking a fifteen-year-old for their weight
and *then* refusing to compute would collect the most sensitive answer
in the flow for nothing — and weight is allowed only inside a voluntary
calculation (§2.2). So both stop-gates fire **before** the anthropometry
questions: age is checked the moment it is given, screening the moment
it is answered. Nobody in a stop scenario is asked their weight.

## The screening step is one question, not three

Pregnancy, nursing, an eating disorder and an illness affecting
nutrition all lead to the *same* outcome — no automatic calculation —
and none of the answers is stored (see ``skill.py``). Splitting them
into three yes/no turns would read as an interrogation and buy nothing:
a person with two of them still lands in the same branch. One
single-select question, with an explicit "none of these" option, is the
same decision with a third of the intrusion.

## Activity sits after weight, before goal (DRF-2102)

The four coefficients are the owner's table (decision of 2026-09-09,
«Коэффициенты активности»: 1.2 / 1.375 / 1.55 / 1.725) — an input of
the calculation, a multiplier on REE, so it is asked with the other
inputs and before the goal, which stays last and closes the flow. Until
this step the skill sent ``1.4`` to everyone — a number outside the
approved set, which the catalogue then rounded to the nearest one. A
fifth answer, «Не знаю», is a skip: the skill sends 1.375 and marks
``activity`` in ``_skipped_fields`` so the catalogue keeps
``health_flags.activity_skipped`` — the number is a default, and it says
so. See :data:`ACTIVITY_SKIP`.

## What is deliberately NOT here yet

* a pace **for «поддержать»**. Pace is asked (CD §72, question 59) only
  when the goal moves the number: the catalogue's goal correction is zero
  for ``maintain``, so a pace question there would ask for something the
  calculation never uses. For «похудеть» / «набрать» it is the step after
  the goal (:data:`PACE_GOALS`). With the catalogue half of question 59 the
  catalogue no longer assumes ``moderate`` and refuses without it; until it
  lands the catalogue still fills its default.
* **Consent is not a step here.** It sits BEFORE the FSM is entered:
  ``skill.py`` (``_on_enter``, #1664, §92) shows the
  ``personal_calculation`` consent screen and only constructs this FSM
  once the consent is recorded — so the first question a person sees is
  the consent, then gender. The screening answer stays outside that
  consent by construction: it is decided and dropped, never stored.
* ``gain_clarify``, ``bmi_ladder``, ``allergies`` / ``meds`` — unchanged
  from the DRF-820 scope note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from apps.skills.fsm import (
    COMPLETE,
    Completed,
    NextStep,
    SkillFSM,
    TransitionResult,
    _Step,
    validate_choice,
    validate_int_range,
)


_GENDER_CHOICES = {"female": "Женский", "male": "Мужской"}
_GOAL_CHOICES = {
    "lose": "Похудеть",
    "maintain": "Поддержать",
    "gain": "Набрать",
}

#: Screening answers. Every value except ``none`` is a §7.1 stop scenario;
#: which one it was is decided and then dropped, never stored (skill.py).
_SCREENING_CHOICES = {
    "none": "Ничего из этого",
    "pregnancy_nursing": "Беременность или кормление",
    "eating_disorder": "Расстройство пищевого поведения",
    "condition": "Заболевание, влияющее на питание",
}

#: The one screening answer that lets the calculation proceed.
SCREENING_CLEAR = "none"

#: Activity answers — the labels are the owner's table verbatim and in its
#: order (``docs/decisions/AYLA_NUTRITION_TARGETS_ARCHITECTURE_DECISION.md``,
#: «Коэффициенты активности»); «Не знаю» is the skip and comes last.
_ACTIVITY_CHOICES = {
    "sedentary": "Почти нет активности",
    "light": "Лёгкая активность",
    "moderate": "Средняя активность",
    "high": "Высокая активность",
    "unknown": "Не знаю",
}

#: slug → coefficient, the four values of §85. The catalogue keeps the same
#: set as ``ACTIVITY_COEFFICIENTS`` in ``nutrition_profile_service``; it is
#: not importable from here, so the mirror is held by the owner decision
#: and guarded in ``tests/test_activity_step_2102.py``.
ACTIVITY_COEFFICIENTS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "high": 1.725,
}

#: The skip answer. Not a coefficient: no number is sent for it (CD §72,
#: question 59) — only ``_skipped_fields: ["activity"]``. With the catalogue
#: half the catalogue clears any earlier activity on it and answers «не
#: хватает данных: активность». Until question 59 the skill sent 1.375 here,
#: a number chosen for the person.
ACTIVITY_SKIP = "unknown"

#: Значения типа питания — СЛОВАРЬ КАТАЛОГА
#: (``users.UserPersonalContext.DietType``), не свой. Состав назвал владелец
#: 23.09.2026 (§77 п. 5); словарь один на два репозитория — решение 24.09,
#: потому что второй разошёлся бы с ним молча. Импортировать нельзя (другой
#: репозиторий), поэтому значения объявлены здесь как контракт провода, и
#: узел `test_diet_step_2310` держит их равенство.
#:
#: «Без ограничений» зовётся ``omnivore``, а не ``unrestricted`` и не
#: ``none``: код из каталога, где в нём лежат данные живых людей, а ``none``
#: там — умолчание столбца, то есть след, а не ответ.
CATALOG_DIET_TYPES: tuple[str, ...] = (
    "omnivore",
    "vegetarian",
    "vegan",
    "keto",
    "halal",
    "kosher",
    "other",
)

#: «Другое словами»: за выбором следует вопрос о самих словах.
DIET_OTHER = "other"

#: «Пропустить» — НЕ ответ «без ограничений» (решение владельца §77 п. 6):
#: первое — отсутствие ответа, второе — ответ. Значения каталога этот слаг не
#: содержат, и в тело он не уходит — уходит пометка пропуска.
DIET_SKIP = "skip"

#: Имя вопроса в ``_skipped_fields`` — короткое, как у остальных пропусков
#: (``weight`` при столбце ``weight_kg``, ``activity`` при
#: ``activity_coefficient``). Пришли сюда имя столбца — каталог пропуск не
#: запишет, в словаре здоровья завёлся бы неизвестный ключ, а человек молча
#: лишился бы живого комментария модели.
DIET_SKIP_WIRE_NAME = "diet"

#: ЧЕРНОВИК ПОДПИСЕЙ. Слова взяты из решения владельца о составе списка;
#: подписи как ВИДИМЫЙ текст он не утверждал (§60) — ждут его слова.
_DIET_CHOICES: dict[str, str] = {
    "omnivore": "Без ограничений",
    "vegetarian": "Вегетарианство",
    "vegan": "Веганство",
    "keto": "Кето",
    "halal": "Халяль",
    "kosher": "Кошер",
    DIET_OTHER: "Другое — напишу словами",
    DIET_SKIP: "Пропустить",
}

#: ЧЕРНОВИК ТЕКСТА: ждёт слова владельца.
DIET_PROMPT = "Есть ли ограничения в еде? Это нужно, чтобы не советовать лишнего."

#: ЧЕРНОВИК ТЕКСТА: ждёт слова владельца.
DIET_NOTE_PROMPT = "Напиши своими словами, чего избегаешь."

#: Pace answers — the catalogue's ``NutritionProfile.Pace`` choices with its
#: labels verbatim («Мягкий» / «Средний»); texts not from a ticket, listed
#: for the owner in the PR.
_PACE_CHOICES = {
    "gentle": "Мягкий",
    "moderate": "Средний",
}

#: Goals whose correction is non-zero, so pace changes the number (catalogue
#: ``GOAL_FACTORS``: lose/tone 0.90, gain 1.10, maintain 1.00). ``tone`` is
#: not offered by this anketa but is named so the rule is the catalogue's.
PACE_GOALS = frozenset({"lose", "gain", "tone"})

#: Below this the calculation is not offered (§7.1). The diary stays.
ADULT_AGE = 18


def _validate_diet_note(user_input: str) -> tuple[Any, str | None]:
    """Слова к «другому»: любой непустой текст. Предела длины здесь нет —
    его никто не называл, а выдуманный резал бы ответ человека молча."""
    text = user_input.strip()
    if not text:
        return None, "Напиши словами, чего избегаешь, — одной строкой."
    return text, None


@dataclass
class AnketaFSM(SkillFSM):
    """7-step nutrition profile FSM. Screening precedes anthropometry."""

    STEPS: ClassVar[dict[str, _Step]] = {
        "gender": _Step(
            prompt=(
                "Какой у тебя пол? Это нужно для расчёта обмена веществ — "
                "у Ж и М разные коэффициенты."
            ),
            validator=validate_choice(_GENDER_CHOICES),
            next="age",
        ),
        "age": _Step(
            prompt="Сколько тебе лет? Напиши число.",
            # Wide on purpose. The range is here to catch "не число" and
            # typos, NOT to enforce the adult gate: rejecting a
            # fifteen-year-old with «возраст должно быть от 18 до 90»
            # would be a refusal without a name, and it would take the
            # diary away from someone the owner decision says keeps it.
            # The gate lives in skill.py and explains itself.
            validator=validate_int_range(1, 120, name="возраст"),
            next="screening",
        ),
        "screening": _Step(
            # Third of seven, and the prompt says so. It used to open with
            # «И последнее перед расчётом» — the owner walked the live
            # path 12.09 01:33 and got height, weight and goal AFTER «the
            # last question». A prompt that names its place wrongly is
            # the same defect class as a number without provenance: the
            # person cannot tell what is coming. The guard in test_skill
            # (``TestScreeningQuestionSitsWhereItSays``) holds the class,
            # not this wording: no step may call itself last unless it is.
            prompt=(
                "Перед ростом и весом — есть ли сейчас что-то из этого? "
                "Спрашиваю потому, что в таких случаях числа должен называть "
                "специалист, а не я."
            ),
            validator=validate_choice(_SCREENING_CHOICES),
            next="height",
        ),
        "height": _Step(
            prompt="Какой у тебя рост в сантиметрах?",
            validator=validate_int_range(100, 220, name="рост"),
            next="weight",
        ),
        "weight": _Step(
            prompt="Какой текущий вес в килограммах?",
            validator=validate_int_range(30, 200, name="вес"),
            next="activity",
        ),
        "activity": _Step(
            prompt=(
                "Сколько движения в твоём обычном дне? От этого зависит "
                "множитель к обмену веществ — а с ним и ориентир."
            ),
            validator=validate_choice(_ACTIVITY_CHOICES),
            next="goal",
        ),
        "goal": _Step(
            prompt="Какая у тебя цель?",
            validator=validate_choice(_GOAL_CHOICES),
            next="diet",
        ),
        # Asked only after «похудеть» / «набрать» — see ``transition``.
        "pace": _Step(
            prompt="В каком темпе идти к цели?",
            validator=validate_choice(_PACE_CHOICES),
            next="diet",
        ),
        # DRF-2310. Замыкает анкету: расчёта не касается, поэтому стоит после
        # всего, что в расчёт входит, — человек получает ориентир, ответив на
        # то, что для него нужно, а не наоборот.
        "diet": _Step(
            prompt=DIET_PROMPT,
            validator=validate_choice(_DIET_CHOICES),
            next=COMPLETE,
        ),
        # Asked only after «другое» — see ``transition``.
        "diet_note": _Step(
            prompt=DIET_NOTE_PROMPT,
            validator=_validate_diet_note,
            next=COMPLETE,
        ),
    }
    INITIAL_STEP: ClassVar[str] = "gender"

    # Dataclass subclass field re-declaration.
    current_step: str = ""
    answers: dict[str, Any] = field(default_factory=dict)
    is_complete: bool = False

    def transition(self, user_input: str) -> TransitionResult:
        """Two steps are conditional: the pace (question 59) and the words.

        After «похудеть» / «набрать» the pace step follows; an earlier pace
        answer (the edit flow re-asks only the goal) is kept. A goal that
        needs no pace drops a pace left from an earlier answer: it would be
        sent for a calculation that does not use it.
        """
        answered = self.current_step
        result = super().transition(user_input)
        if answered == "diet" and self.answers.get("diet") == DIET_OTHER:
            # «Другое» без слов — не ответ: сказано ничего. Спрашиваем слова.
            self.is_complete = False
            self.current_step = "diet_note"
            return NextStep(prompt=self.STEPS["diet_note"].prompt)
        if answered in ("goal", "pace") and self.answers.get("diet"):
            # Питание уже названо — второй раз не спрашиваем. Это бывает,
            # когда страж скилла вернул человека к пропущенной активности
            # (состояние сохранено до её появления): цепочка шагов провела бы
            # его через питание снова, и он отвечал бы дважды на один вопрос.
            self.is_complete = True
            self.current_step = ""
            if answered == "goal" and self.answers.get("goal") not in PACE_GOALS:
                self.answers.pop("pace", None)
            return Completed(answers=dict(self.answers))
        if answered != "goal":
            return result
        if self.answers.get("goal") in PACE_GOALS:
            if not self.answers.get("pace"):
                self.is_complete = False
                self.current_step = "pace"
                return NextStep(prompt=self.STEPS["pace"].prompt)
            return result
        # Цель, которой темп не нужен, уносит темп от прежнего ответа: он
        # ушёл бы в расчёт, который его не использует.
        self.answers.pop("pace", None)
        return result


# ─── public helpers ──────────────────────────────────────────────────────


GENDER_CHOICES = _GENDER_CHOICES
GOAL_CHOICES = _GOAL_CHOICES
SCREENING_CHOICES = _SCREENING_CHOICES
ACTIVITY_CHOICES = _ACTIVITY_CHOICES
PACE_CHOICES = _PACE_CHOICES
DIET_CHOICES = _DIET_CHOICES


def choice_keyboard_options(step: str) -> list[tuple[str, str]]:
    """Return ``[(label, slug), ...]`` for a step that uses a choice keyboard.

    Used by the skill to call :func:`apps.orchestrator.ui.keyboards.anketa_choice_keyboard`
    with the right options per step.

    Raises:
        KeyError: step has no choice keyboard (caller should use text input).
    """
    if step == "diet":
        return [(label, slug) for slug, label in _DIET_CHOICES.items()]
    if step == "gender":
        return [(label, slug) for slug, label in _GENDER_CHOICES.items()]
    if step == "goal":
        return [(label, slug) for slug, label in _GOAL_CHOICES.items()]
    if step == "pace":
        return [(label, slug) for slug, label in _PACE_CHOICES.items()]
    if step == "screening":
        # «Ничего из этого» first: it is the common answer, and putting
        # the conditions above it would make the neutral path the one you
        # scroll past.
        return [(label, slug) for slug, label in _SCREENING_CHOICES.items()]
    if step == "activity":
        # The owner's order, «Не знаю» last: the skip is an escape, not a
        # level, and it must not read as the fifth rung of the ladder.
        return [(label, slug) for slug, label in _ACTIVITY_CHOICES.items()]
    raise KeyError(f"step {step!r} has no choice keyboard (text-input step)")


CHOICE_STEPS = frozenset({"gender", "goal", "screening", "activity", "pace", "diet"})
"""Steps that present a choice keyboard. Text-input steps are the complement."""
