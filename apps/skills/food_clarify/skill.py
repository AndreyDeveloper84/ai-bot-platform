"""Food-clarify skill — DRF-358 fallback card.

Sprint 9 / P4 (DRF-821). When a short message looks like a food/drink
log attempt (``looks_like_food_drink``) the skill takes the turn and
emits a 2-button card:

* **📔 В дневник** → ``cb:food:diary`` — hands off to ``food_scanner``
  with a "send a photo" prompt (text-only food parsing is not in scope
  for Phase 0; the upstream nutrition_anketa ladder lives in skills/
  nutrition_anketa).
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
from apps.skills.food_clarify.hints import looks_like_food_drink
from apps.skills.registry import register

CARD_TEXT = (
    "Это про еду или напиток? Если да — давай добавлю в дневник. "
    "Если просто описался — отметь «опечатка»."
)

DIARY_PROMPT = (
    "📸 Скинь фото блюда — посчитаю калории и БЖУ. "
    "Через текст пока не умею распознавать (это в работе)."
)

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
        if text in ("cb:food:diary", "cb:food:typo"):
            return True
        # Free-text path — only when the hint detector says yes.
        return looks_like_food_drink(text)

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()

        if text == "cb:food:diary":
            return SkillResult(
                reply_text=DIARY_PROMPT,
                meta={"reply_kind": "food_clarify_diary"},
            )

        if text == "cb:food:typo":
            return SkillResult(
                reply_text=TYPO_ACK,
                meta={"reply_kind": "food_clarify_typo"},
            )

        # Free-text → emit the card.
        return SkillResult(
            reply_text=CARD_TEXT,
            action_type="food_clarify_card",
            action_data={"buttons": food_drink_clarify_keyboard()},
            meta={"reply_kind": "food_clarify_card"},
        )
