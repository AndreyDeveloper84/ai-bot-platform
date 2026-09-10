"""Nutrition anketa skill — 5-step nutrition profile FSM.

Sprint 9 / P3 (DRF-820). Largest port in Sprint 9: walks the user
through gender → age → height → weight → goal, persists state in
``Conversation.skill_state['nutrition_anketa']`` (via D3), POSTs the
result to Ayla ``upsert_profile``, and renders the computed norms.

## State machine

Backed by :class:`apps.skills.nutrition_anketa.fsm.AnketaFSM`. State
serialised to JSON between turns; resumed via
:meth:`SkillFSM.deserialize`.

## Skill state contract

* Key in ``conversation.skill_state``: ``"nutrition_anketa"``
* Value: ``AnketaFSM.serialize()`` dict
* Empty / missing → fresh FSM (first turn)

The skill writes state back to the conversation after each turn. The
pipeline does NOT auto-persist (per D3 design — explicit writers).

## Entry / resume / edit

* **Entry** — match on ``cb:anketa:start`` OR the literal command
  ``/anketa`` (so a bare command can launch the flow).
* **Resume** — when an active FSM lives in skill_state, the skill
  claims plain-text + choice callbacks until COMPLETE.
* **Edit** — :func:`AnketaFSM.goto` is exposed for ``cb:anketa:edit:{step}``.
  Phase 1 wires the edit button into the summary card; Sprint 9
  delivers the callback handler only.

## Completion

On COMPLETE, the skill:

1. Calls Ayla ``upsert_profile`` with the answers + ``activity_coefficient=1.4``
   (sedentary default; Phase 1 will collect activity as a separate step).
2. Reads the response's ``norms`` envelope (kcal / protein / fat /
   carbs / water_ml).
3. Renders a "норму посчитала" summary.
4. Wipes the FSM state from ``conversation.skill_state``.

## Stop scenarios (owner decision 2026-09-09, §7.1)

Ayla does not compute targets automatically for a minor, during
pregnancy or nursing, for a declared eating disorder, or for a condition
affecting nutrition or fluid balance. **The diary stays available** —
that is the point of the branch, not a consolation.

Two gates, both firing on the turn the answer arrives:

* after ``age`` — under :data:`~apps.skills.nutrition_anketa.fsm.ADULT_AGE`;
* after ``screening`` — any answer other than ``none``.

Three properties this branch must keep, each of which cost something to
learn elsewhere in this codebase:

1. **Nothing is sent to Ayla.** Not "sent and ignored" — not sent.
   ``profile_upsert_service._recompute_and_persist`` recomputes on every
   upsert unconditionally, and the current override ladder *adds* kcal
   and water for pregnancy and nursing (+200/+400 kcal, +300/+700 ml).
   Forwarding those answers today would make the outcome worse than not
   asking at all. The ladder is being replaced by the calculation
   service; until it is, the stop ends before the network call.
2. **The screening answer is not stored.** It decides, then it is
   dropped — not written to ``skill_state``, not put in ``health_flags``,
   not remembered. It is special-category data under 152-ФЗ and the
   consent that would allow keeping it is still an open question with
   the owner. Same treatment as «что не подошло» in
   :mod:`apps.skills.food_correction`.
3. **The log records the bucket, not the condition.** ``stop=screening``
   and never *which* — a log line is storage too.

## Scope cuts vs mysite

Deferred to Phase 1 / a follow-up Sprint 9 ticket:

* Pace step (slow / balanced) — and with it the "faster than ~0.9 kg per
  week" stop, which needs a methodology that computes a rate.
* Gain-clarify branch.
* BMI-ladder override response handling ("Учла важное" override card).
* Allergies / meds (these belong with the Tier-B health screening
  port — see DRF-824 scope note).
* Activity step — defaulted to ``1.4`` (sedentary). Phase 1 makes it a
  step with 5 levels. Note ``1.4`` is not one of the four coefficients
  the owner approved (1.2 / 1.375 / 1.55 / 1.725); the calculation
  service change carries that.
* Consent screen before the weight question. The owner requires a
  separate consent for weight; whether the same consent covers the
  screening answers is open (question 1 in
  ``docs/PLAN_NUTRITION_TARGETS.md``). Wiring one checkbox that silently
  covers both is the thing not to do, so it lands separately.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from apps.integrations.ayla import (
    NutritionAPIError,
    NutritionUnavailableError,
    external_user_id_for,
    get_nutrition_client,
)
from apps.orchestrator.ui.keyboards import anketa_choice_keyboard, parse_callback
from apps.skills.base import SkillContext, SkillResult
from apps.skills.fsm import Completed, NextStep
from apps.skills.nutrition_anketa.fsm import (
    ADULT_AGE,
    CHOICE_STEPS,
    SCREENING_CLEAR,
    AnketaFSM,
    choice_keyboard_options,
)
from apps.skills.registry import register

logger = logging.getLogger(__name__)

# Key inside Conversation.skill_state.
_STATE_KEY = "nutrition_anketa"

_AYLA_DOWN_FALLBACK = (
    "Не получилось сохранить — сервис временно недоступен. Попробуй ещё раз через минуту."
)

# ─── stop-scenario copy (§7.1) ───────────────────────────────────────────
#
# Two texts, not one. The reason differs, and a single «не считаю»
# covering both would be a refusal without a name — the person could not
# tell whether we mean their age or their answer, and neither could we.

#: Shared tail. The diary staying open IS the decision, so it is stated
#: plainly rather than tacked on as an apology.
_STOP_DIARY_TAIL = (
    "Дневник остаётся: записывай еду и воду, я посчитаю и покажу, сколько "
    "вышло за день. Без дневной цели и без процентов.\n\n"
    "Если ориентир тебе назначил специалист, вписать его пока некуда — "
    "такой ручки у меня ещё нет."
)

_STOP_MINOR = (
    "Персональный ориентир по калориям и воде я считаю только совершеннолетним. "
    "Это не про тебя лично — просто такие числа несовершеннолетнему должен "
    "называть врач, а не бот.\n\n" + _STOP_DIARY_TAIL
)

#: Deliberately does not repeat back what the person just declared.
_STOP_SCREENING = (
    "Тогда персональный ориентир считать не буду — в таких случаях числа должен "
    "называть специалист, который тебя ведёт.\n\n" + _STOP_DIARY_TAIL
)

_STOP_TEXTS = {"minor": _STOP_MINOR, "screening": _STOP_SCREENING}


@register
class NutritionAnketaSkill:
    """5-step nutrition profile collection."""

    name: ClassVar[str] = "nutrition_anketa"

    # ─── match ──────────────────────────────────────────────────────────

    def matches(self, context: SkillContext) -> bool:
        text = context.message_text.strip()

        # Entry path — explicit command OR start callback.
        if text == "/anketa" or text == "cb:anketa:start":
            return True

        # Resume path — claim turns while an FSM is in flight.
        if self._has_active_fsm(context):
            # Anketa choice callback or plain user input.
            if text.startswith("cb:anketa:choice:") or not text.startswith("cb:"):
                return True

        # Edit callback — channel adapter routes after summary card shown.
        if text.startswith("cb:anketa:edit:"):
            return True

        return False

    # ─── handle ──────────────────────────────────────────────────────────

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()

        # Entry: start fresh FSM.
        if text in ("/anketa", "cb:anketa:start"):
            return self._on_enter(context)

        # Edit: jump back to a step.
        if text.startswith("cb:anketa:edit:"):
            return self._on_edit(context, text)

        # Resume — load FSM + transition.
        return self._on_transition(context, text)

    # ─── entry ──────────────────────────────────────────────────────────

    def _on_enter(self, context: SkillContext) -> SkillResult:
        fsm = AnketaFSM()
        step_result = fsm.enter()
        self._save_state(context, fsm)
        return self._render_step(fsm.current_step, step_result.prompt)

    # ─── edit (cb:anketa:edit:{step}) ────────────────────────────────────

    def _on_edit(self, context: SkillContext, text: str) -> SkillResult:
        # Parse cb:anketa:edit:{step}. The D2 parse_callback splits to
        # action="edit", ref="{step}".
        parsed = parse_callback(text)
        if parsed is None or not parsed.get("ref"):
            return SkillResult(reply_text="", should_send=False)
        step = parsed["ref"]
        fsm = self._load_or_new(context)
        try:
            step_result = fsm.goto(step)
        except ValueError:
            # Unknown step — defensive.
            return SkillResult(reply_text="", should_send=False)
        self._save_state(context, fsm)
        return self._render_step(fsm.current_step, step_result.prompt)

    # ─── transition ──────────────────────────────────────────────────────

    def _on_transition(self, context: SkillContext, text: str) -> SkillResult:
        fsm = self._load_or_new(context)
        if not fsm.current_step:
            # No active FSM but match() claimed the turn — defensive enter.
            return self._on_enter(context)

        # Extract user input — from choice callback if present, else raw text.
        value = self._extract_value(text)

        # Which step this answer belongs to — `transition` advances
        # `current_step`, so it has to be read before the call.
        answered_step = fsm.current_step
        result = fsm.transition(value)

        # §7.1 stop-gates. Checked here, on the turn the answer arrives:
        # before the next question is asked (so nobody in a stop scenario
        # is asked their weight) and before any call to Ayla.
        stop = self._stop_reason(answered_step, fsm.answers, result)
        if stop is not None:
            self._clear_state(context)
            return self._render_stop(stop, context)

        if answered_step == "screening":
            # Decided above; not kept. Popping before `_save_state` is what
            # keeps it out of `Conversation.skill_state` — and out of the
            # `answers` copy that `Completed` hands to `_on_complete`, so
            # it cannot reach the payload either.
            fsm.answers.pop("screening", None)

        if isinstance(result, NextStep):
            self._save_state(context, fsm)
            return self._render_step(fsm.current_step, result.prompt)

        # Completed → POST to Ayla.
        assert isinstance(result, Completed)
        return self._on_complete(context, result.answers)

    # ─── stop scenarios (§7.1) ───────────────────────────────────────────

    def _stop_reason(
        self,
        answered_step: str,
        answers: dict,
        result: NextStep | Completed,
    ) -> str | None:
        """Name of the stop scenario this answer triggers, or ``None``.

        Returns a bucket name (``"minor"`` / ``"screening"``), never the
        declared condition: the bucket is all the caller needs, and the
        condition is special-category data we have decided not to carry.
        """
        if isinstance(result, NextStep) and result.is_validation_error:
            # Nothing was stored — the FSM re-asks the same step. Reading
            # `answers` here would test the PREVIOUS answer to this step
            # and stop on an input the person has not actually given.
            return None

        if answered_step == "age":
            age = answers.get("age")
            if isinstance(age, int) and age < ADULT_AGE:
                return "minor"

        if answered_step == "screening" and answers.get("screening") != SCREENING_CLEAR:
            return "screening"

        return None

    def _render_stop(self, reason: str, context: SkillContext) -> SkillResult:
        """State 4 of the owner's twelve: no automatic result, diary intact."""
        # Bucket only. `stop=screening` says everything the operator needs;
        # `stop=pregnancy_nursing` would put a health fact in the log file.
        logger.info(
            "anketa.stop_scenario reason=%s conv=%s",
            reason,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=_STOP_TEXTS[reason],
            action_type="anketa_stop",
            action_data={
                "reason": reason,
                # The same two next moves the completed anketa offers. The
                # branch says the diary stays open, so it has to open it.
                "buttons": _post_anketa_chips(),
            },
            meta={"reply_kind": f"anketa_stop_{reason}"},
        )

    def _on_complete(self, context: SkillContext, answers: dict) -> SkillResult:
        external_id = external_user_id_for(context.bot_user)
        payload = self._build_ayla_payload(answers)

        try:
            profile = asyncio.run(
                get_nutrition_client().upsert_profile(
                    external_user_id=external_id,
                    data=payload,
                )
            )
        except NutritionUnavailableError:
            logger.warning("anketa.ayla_unavailable user=%s", external_id)
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "anketa_ayla_down"},
            )
        except NutritionAPIError:
            logger.exception("anketa.ayla_api_error user=%s", external_id)
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "anketa_ayla_error"},
            )

        # Wipe FSM state — anketa is one-shot per user.
        self._clear_state(context)

        return SkillResult(
            reply_text=_format_summary(profile),
            action_type="anketa_complete",
            action_data={
                "daily_kcal": profile.daily_kcal,
                "protein_g": profile.protein_g,
                "fat_g": profile.fat_g,
                "carbs_g": profile.carbs_g,
                "water_ml": profile.water_ml,
                "goal_overridden_by": profile.goal_overridden_by,
                # DRF-1302 — the anketa used to compute the norms and go
                # silent, leaving the person holding five numbers and no next
                # move. These two are the only next moves that exist as
                # working code today, and each callback is claimed by a
                # deterministic matcher (WaterSkill's beverage parser;
                # ``looks_like_diary_request``), so a tap runs rather than
                # asks a model to guess. A photo cannot be a chip -- the
                # person has to send one -- so the closing line invites it in
                # words instead of a button that could not deliver.
                "buttons": _post_anketa_chips(),
            },
            meta={"reply_kind": "anketa_complete"},
        )

    # ─── helpers ────────────────────────────────────────────────────────

    def _has_active_fsm(self, context: SkillContext) -> bool:
        raw = getattr(context.conversation, "skill_state", None) or {}
        bucket = raw.get(_STATE_KEY) if isinstance(raw, dict) else None
        return bool(bucket and bucket.get("current_step"))

    def _load_or_new(self, context: SkillContext) -> AnketaFSM:
        raw = getattr(context.conversation, "skill_state", None) or {}
        bucket = raw.get(_STATE_KEY) if isinstance(raw, dict) else None
        return AnketaFSM.deserialize(bucket)  # type: ignore[return-value]

    def _save_state(self, context: SkillContext, fsm: AnketaFSM) -> None:
        """Persist FSM state to the conversation row.

        Per D3 contract the skill explicitly writes — pipeline doesn't
        auto-persist. We use ``update_fields`` to limit the write to
        just the JSON column.
        """
        conversation = context.conversation
        # Conversations retro B1: route through the atomic helper so
        # two concurrent skills writing different sub-keys can't
        # clobber each other. The helper handles the select_for_update
        # + read-modify-write inside a single transaction.
        if _is_real_orm_conversation(conversation):
            from apps.conversations.services import write_skill_state

            write_skill_state(conversation, _STATE_KEY, fsm.serialize())
            return

        # Mock / synthetic conversations (used by isolated unit tests that
        # don't spin up the ORM): fall back to in-memory mutation.
        raw = getattr(conversation, "skill_state", None) or {}
        if not isinstance(raw, dict):
            raw = {}
        raw[_STATE_KEY] = fsm.serialize()
        conversation.skill_state = raw
        save = getattr(conversation, "save", None)
        if callable(save):
            save(update_fields=["skill_state"])

    def _clear_state(self, context: SkillContext) -> None:
        conversation = context.conversation
        if _is_real_orm_conversation(conversation):
            from apps.conversations.services import write_skill_state

            write_skill_state(conversation, _STATE_KEY, None)
            return

        raw = getattr(conversation, "skill_state", None) or {}
        if isinstance(raw, dict) and _STATE_KEY in raw:
            conversation.skill_state = {k: v for k, v in raw.items() if k != _STATE_KEY}
            save = getattr(conversation, "save", None)
            if callable(save):
                save(update_fields=["skill_state"])

    def _extract_value(self, text: str) -> str:
        """Pull the value from a ``cb:anketa:choice:{step}:{value}`` callback
        OR return the raw text. Plain numbers and free-text pass through."""
        if not text.startswith("cb:anketa:choice:"):
            return text
        parsed = parse_callback(text)
        if parsed is None:
            return text
        ref = parsed.get("ref") or ""
        # ref shape is "{step}:{value}" — return just the value half.
        _step, _, value = ref.partition(":")
        return value or text

    def _render_step(self, step: str, prompt: str) -> SkillResult:
        action_data: dict = {"step": step}
        if step in CHOICE_STEPS:
            action_data["buttons"] = anketa_choice_keyboard(step, choice_keyboard_options(step))
        return SkillResult(
            reply_text=prompt,
            action_type=f"anketa_step_{step}",
            action_data=action_data,
            meta={"reply_kind": f"anketa_{step}"},
        )

    def _build_ayla_payload(self, answers: dict) -> dict:
        """Map FSM answers to the Ayla profile schema.

        Activity is hardcoded to ``1.4`` (sedentary) for Sprint 9.
        Phase 1 collects activity as a step.
        """
        return {
            "gender": answers["gender"],
            "age": int(answers["age"]),
            "height_cm": int(answers["height"]),
            "weight_kg": int(answers["weight"]),
            "goal": answers["goal"],
            "activity_coefficient": 1.4,
        }


# ─── helpers ──────────────────────────────────────────────────────────────


def _is_real_orm_conversation(conversation: object) -> bool:
    """True iff ``conversation`` is a real :class:`apps.conversations.models.Conversation` instance.

    Skills test with synthetic ``_StatefulConversation`` classes that do
    NOT subclass ``Conversation`` — the atomic
    :func:`apps.conversations.services.write_skill_state` helper would
    raise ``ValueError`` on those (no tenant_id, no ORM row). Use
    ``isinstance`` so a bare ``MagicMock()`` (whose attribute access
    returns truthy MagicMocks) doesn't get mis-routed into the ORM
    path — closes reviewer Y-1.
    """

    from apps.conversations.models import Conversation

    return isinstance(conversation, Conversation)


# ─── summary rendering ────────────────────────────────────────────────────


def _post_anketa_chips() -> list[dict[str, str]]:
    """The two next steps that really execute after the norms land.

    Kept next to the summary it ships with, and built from the
    ``personal_surface`` constants so the labels and callbacks cannot drift
    apart from the matchers that claim them.
    """

    from apps.orchestrator.personal_surface import CHIP_DIARY, CHIP_WATER, diary_is_reachable

    chips = [dict(CHIP_WATER)]
    if diary_is_reachable():
        # Only the global path claims «что я ел сегодня» deterministically.
        # On a salon bot the same tap would fall through the skill ladder to a
        # generic reply -- a button that answers «я вас не понял», which is
        # worse than no button.
        chips.append(dict(CHIP_DIARY))
    return chips


#: Строки карточки: подпись, поле профиля, единица. Порядок значим —
#: он же порядок на экране.
_SUMMARY_ROWS: tuple[tuple[str, str, str], ...] = (
    ("🔥 Калории", "daily_kcal", "ккал/день"),
    ("🍗 Белок", "protein_g", "г"),
    ("🥑 Жиры", "fat_g", "г"),
    ("🍚 Углеводы", "carbs_g", "г"),
    ("💧 Вода", "water_ml", "мл"),
)


def _format_summary(profile) -> str:
    """Карточка после анкеты — только те ориентиры, которые ЕСТЬ.

    Здесь стояли пять строк подряд, безусловно. Каждая печатала
    `{profile.<поле>}`, и ноль печатался как значение: «💧 Вода: 0 мл»,
    а у человека, не назвавшего вес, — ещё и «🔥 Калории: 0 ккал/день».
    Ноль ккал в сутки не бывает; такая карточка не «пустая», она ЛЖЁТ, и
    лжёт в самом громком месте — сразу после «Готово, рассчитала твои
    нормы».

    С 09.09.2026 нолей в этих полях штатно много: владелец снял формулу
    воды `30 мл × вес` (§82) и подстановку медианы за пропущенные
    рост/вес/возраст/пол (§85, раздел 3.2 — входы обязательны). Строка
    без значения теперь не печатается вовсе, а если не посчиталось
    НИЧЕГО — карточка не притворяется расчётом.

    Ноль читается как отсутствие, а не как «ориентир ноль»: ни одна из
    пяти величин не может быть нулём у живого человека.
    """
    rows = [
        f"{label}: {value} {unit}"
        for label, field, unit in _SUMMARY_ROWS
        if (value := int(getattr(profile, field, 0) or 0)) > 0
    ]

    if not rows:
        # Ни одного ориентира — говорим об этом прямо и не называем
        # причину чужими словами: у отсутствия должно быть имя, но имя
        # это «мы не считали», а не «ты чего-то не заполнил».
        return (
            "Дневник готов — записывай еду и воду, я всё сохраню.\n"
            "Дневных ориентиров пока не считаю."
        )

    parts: list[str] = ["Готово, рассчитала твои нормы:"]
    parts.append("\n".join(rows))
    if profile.goal_overridden_by:
        # Ayla applied a safety override (pregnancy / eating-disorder /
        # BMI floor). Mention it gently — the override is the right call,
        # not a downgrade.
        parts.append("Учла важное в анамнезе — нормы подобрала с поправкой на это.")
    parts.append("Теперь могу считать калории и БЖУ из фото блюд.")
    return "\n\n".join(parts)
