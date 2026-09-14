"""Food-clarify skill — DRF-358 fallback card.

Sprint 9 / P4 (DRF-821). When a short message looks like a food/drink
log attempt (``looks_like_food_drink``) the skill takes the turn and
emits a 2-button card:

* **📔 В дневник** → ``cb:food:diary`` — since DRF-1837 estimates the
  phrase the person just typed and shows «Я распознала так»
  (:mod:`apps.skills.food_clarify.text_entry`, §109). Until then this
  said «Скинь фото — через текст пока не умею», and with the photo flag
  off that closed the ring: nothing could be logged at all.
* **❌ Опечатка** → ``cb:food:typo`` — silent soft-ack to avoid
  fatiguing the user on false positives.

The skill must run **before** the AI Concierge / FAQ skill so the LLM
never sees "Борщ 300г" and produces a cold "не могу с заказом" — that
was the DRF-358 incident dev-bot 2026-05-08 09:10. Order is enforced
by registration sequence in :mod:`apps.skills.apps`.

## Callback contract

The two callbacks are emitted from :func:`apps.orchestrator.ui.keyboards.food_drink_clarify_keyboard`
(D2). Decoded by ``parse_callback`` → ``{"domain": "food", "action": "diary"}``.
Channel adapter routes by ``cb:food:*`` prefix to this skill OR to the
P1 food_scanner skill (once it ships).
"""

from __future__ import annotations

from typing import ClassVar

from apps.orchestrator.ui.keyboards import food_drink_clarify_keyboard
from apps.skills.base import SkillContext, SkillResult
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.hints import looks_like_food_drink
from apps.skills.registry import register

CARD_TEXT = (
    "Это про еду или напиток? Если да — давай добавлю в дневник. "
    "Если просто описался — отметь «опечатка»."
)

#: Kept under its old name for callers; the text changed with DRF-1837 —
#: asked only when the tap arrives without the phrase it was about.
DIARY_PROMPT = text_entry.ASK_WHAT_TEXT

TYPO_ACK = "Поняла 🙂"


@register
class FoodClarifySkill:
    """Pre-LLM food/drink hint card. Cheap regex; no network."""

    name: ClassVar[str] = "food_clarify"

    def matches(self, context: SkillContext) -> bool:
        """Match on short food-shaped text + the two callback IDs.

        The free-text path is the DRF-358 fix; the callback path handles
        the user's click on the card emitted on a previous turn.
        """
        text = context.message_text.strip()
        # Callback path — channel adapter has already decoded the inline
        # button click into a "cb:food:diary" / "cb:food:typo" text body
        # (per the platform's channel-agnostic callback contract).
        if text in ("cb:food:diary", "cb:food:typo") or text in text_entry.TEXT_CALLBACKS:
            return True
        # DRF-1837 — an answer to the question this skill asked (grams, or
        # «что было»). Checked before the hint: «250» is not food-shaped.
        if text_entry.claims_text(context.conversation, text):
            return True
        # Free-text path — only when the hint detector says yes.
        return looks_like_food_drink(text)

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()

        if text in text_entry.TEXT_CALLBACKS:
            return text_entry.on_callback(context, text)

        if text == "cb:food:diary":
            return text_entry.on_diary_tap(context)

        if text == "cb:food:typo":
            text_entry.forget(context)
            return SkillResult(
                reply_text=TYPO_ACK,
                meta={"reply_kind": "food_clarify_typo"},
            )

        if text_entry.claims_text(context.conversation, text):
            return text_entry.on_text(context, text)

        # Free-text → remember the phrase for the «📔 В дневник» tap, emit the card.
        text_entry.remember_source(context, text)
        return SkillResult(
            reply_text=CARD_TEXT,
            action_type="food_clarify_card",
            action_data={"buttons": food_drink_clarify_keyboard()},
            meta={"reply_kind": "food_clarify_card"},
        )
