"""Food-correction skill — prompts for the 3 correction fields.

Sprint 9 / P5 (DRF-822). Entered via the ``✏️ Уточнить`` button on a
food_scanner (P1) recognition card. The button emits one of three
callbacks (built by D2 ``correction_choice_keyboard``); this skill
turns each callback into the corresponding follow-up prompt:

* ``cb:food:correct:grams:{scan_id}`` → "Введи правильный вес в граммах"
* ``cb:food:correct:name:{scan_id}``  → "Напиши, что было — поправлю"
* ``cb:food:correct:macros:{scan_id}`` → "Какие БЖУ нужны? Напиши: белки/жиры/углеводы"

## Scope cut — apply path stays in Phase 1

The mysite ``legacy_maxbot/handlers/food_correction.py`` (361 LOC) is a
full FSM with 13 callbacks: portion picker → Ayla re-scan with
``portion_multiplier``, dish rename → re-recognize, manual macros →
update entry. Sprint 9 / P5 ships only the **prompt** half because
the apply path needs two things we don't have yet:

1. **An Ayla "update diary entry" endpoint.** The mysite version
   re-runs ``scan_photo(portion_multiplier=...)`` which requires the
   original image bytes (gone after the first scan). A cleaner
   approach is a dedicated ``update_diary_entry(log_id, portion_g, ...)``
   call — to be added to ``apps.integrations.ayla.nutrition_client`` in
   Phase 1 (DRF-825 follow-up).

2. **Skill-state plumbing for "waiting for correction value".** The D3
   SkillFSM helper lands the state machine; what's missing is the
   pipeline-side write-back of ``conversation.skill_state``. P3
   nutrition_anketa is the first heavy consumer; P5 will adopt the
   same path once that wiring exists.

In the meantime the user's free-text response after a P5 prompt falls
through to the AI Concierge (or echo skill). The bot acknowledges
that the correction was noted; manual diary cleanup is a manager task
in the staff workflow. This is consistent with the mysite source on
the EPIC-Q backlog ("correction stays in human hands until LLM cost
guard").

## Why this skill exists at all

Without P5 the ``cb:food:correct:*`` callback would land in the echo
skill and produce a confusing "cb:food:correct:grams:scan-1" verbatim
echo. P5 turns the callback into an empathic prompt and tells the
user the next step.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from apps.orchestrator.ui.keyboards import parse_callback
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# ─── prompts ──────────────────────────────────────────────────────────────


_PROMPTS: dict[str, str] = {
    "grams": ("Сколько на самом деле было в граммах? Напиши число, пересчитаю калории и БЖУ."),
    "name": ("Что было на фото? Напиши коротко — поправлю в дневнике."),
    "macros": (
        "Окей, ручной ввод. Напиши макросы через слэш: "
        "белки/жиры/углеводы (грамм). Например: 12/8/32"
    ),
}


# ─── skill ────────────────────────────────────────────────────────────────


@register
class FoodCorrectionSkill:
    """Three correction prompts for the food_scanner correction flow."""

    name: ClassVar[str] = "food_correction"

    def matches(self, context: SkillContext) -> bool:
        text = context.message_text.strip()
        if not text.startswith("cb:food:correct:"):
            return False
        parsed = parse_callback(text)
        if parsed is None:
            return False
        if parsed["action"] != "correct":
            return False
        ref = parsed.get("ref") or ""
        # ref shape is "{field}:{scan_id}" — split on the first colon.
        field, _, _scan_id = ref.partition(":")
        return field in _PROMPTS

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        parsed = parse_callback(text)
        if parsed is None:
            return SkillResult(reply_text="", should_send=False)

        ref = parsed.get("ref") or ""
        field, _, scan_id = ref.partition(":")
        prompt = _PROMPTS.get(field)
        if not prompt:
            # matches() guards this; defensive empty.
            return SkillResult(reply_text="", should_send=False)

        logger.info(
            "food_correction.prompted field=%s scan=%s conversation=%s",
            field,
            scan_id,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=prompt,
            action_type="food_correction_prompt",
            action_data={"field": field, "scan_id": scan_id},
            meta={"reply_kind": f"food_correction_{field}"},
        )
