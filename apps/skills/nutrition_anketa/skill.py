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

## Scope cuts vs mysite

Deferred to Phase 1 / a follow-up Sprint 9 ticket:

* Pace step (slow / balanced).
* Gain-clarify branch.
* BMI-ladder override response handling ("Учла важное" override card).
* Allergies / meds (these belong with the Tier-B health screening
  port — see DRF-824 scope note).
* Activity step — defaulted to ``1.4`` (sedentary). Phase 1 makes it a
  step with 5 levels.
* Consent screen — assumed handled at tenant onboarding; not per-skill.
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
    CHOICE_STEPS,
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

        result = fsm.transition(value)

        if isinstance(result, NextStep):
            self._save_state(context, fsm)
            return self._render_step(fsm.current_step, result.prompt)

        # Completed → POST to Ayla.
        assert isinstance(result, Completed)
        return self._on_complete(context, result.answers)

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
        raw = getattr(conversation, "skill_state", None) or {}
        if not isinstance(raw, dict):
            raw = {}
        raw[_STATE_KEY] = fsm.serialize()
        conversation.skill_state = raw
        # `save` may not be available on a Mock in tests — guard.
        save = getattr(conversation, "save", None)
        if callable(save):
            save(update_fields=["skill_state"])

    def _clear_state(self, context: SkillContext) -> None:
        conversation = context.conversation
        raw = getattr(conversation, "skill_state", None) or {}
        if isinstance(raw, dict) and _STATE_KEY in raw:
            raw = {k: v for k, v in raw.items() if k != _STATE_KEY}
            conversation.skill_state = raw
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


# ─── summary rendering ────────────────────────────────────────────────────


def _format_summary(profile) -> str:
    """Compose the post-anketa norms card."""
    parts: list[str] = ["Готово, рассчитала твои нормы:"]
    parts.append(
        f"🔥 Калории: {profile.daily_kcal} ккал/день\n"
        f"🍗 Белок: {profile.protein_g} г\n"
        f"🥑 Жиры: {profile.fat_g} г\n"
        f"🍚 Углеводы: {profile.carbs_g} г\n"
        f"💧 Вода: {profile.water_ml} мл"
    )
    if profile.goal_overridden_by:
        # Ayla applied a safety override (pregnancy / eating-disorder /
        # BMI floor). Mention it gently — the override is the right call,
        # not a downgrade.
        parts.append("Учла важное в анамнезе — нормы подобрала с поправкой на это.")
    parts.append("Теперь могу считать калории и БЖУ из фото блюд.")
    return "\n\n".join(parts)
