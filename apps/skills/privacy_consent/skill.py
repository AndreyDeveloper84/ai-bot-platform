"""PrivacyConsentSkill (DRF-469 / Sprint 3 / D2).

Surfaces 152-ФЗ / GDPR data-subject rights through the chat:

  * "удалить мои данные" / "delete my data" / "удалить меня" →
    `data_delete(bot_user)` + close the conversation.
  * "выгрузить мои данные" / "export my data" →
    `data_export(bot_user)` → JSON archive surfaced in the reply.

Match keyword set is conservative on purpose — these triggers cause
destructive action, so false positives would be expensive.
Sprint 4+ adds an intent classifier that can soften the match.

### Why this skill ships first

Sprint 3 plan locked decision: privacy comes before the booking and
nutrition skills because compliance failure on a regulated workflow
is a structural blocker for go-live. Shipping it as the first
"real" skill means the dispatcher pattern gets exercised end-to-end
on Day 1.
"""

from __future__ import annotations

import json
import logging
from typing import ClassVar

from apps.events.services import emit
from apps.events.vocabulary import SKILL_DISPATCHED
from apps.skills.base import SkillContext, SkillResult
from apps.skills.privacy_consent.tools import data_delete, data_export
from apps.skills.registry import register

logger = logging.getLogger(__name__)

# Keyword tuples. Substring match on `message_text.lower()`. Russian
# verb forms enumerated because Sprint 3 ships no stemmer — Sprint 4+
# intent classifier replaces this.
_DELETE_KEYWORDS: tuple[str, ...] = (
    "удалить мои данные",
    "удалите мои данные",
    "удали мои данные",
    "удалить меня",
    "удалите меня",
    "удали меня",
    "delete my data",
    "delete me",
)
_EXPORT_KEYWORDS: tuple[str, ...] = (
    "выгрузить мои данные",
    "выгрузите мои данные",
    "скачать мои данные",
    "скачайте мои данные",
    "export my data",
    "download my data",
)


@register
class PrivacyConsentSkill:
    """Trigger: explicit user request to export or delete their data."""

    name: ClassVar[str] = "privacy_consent"

    @staticmethod
    def _intent_for(text: str) -> str | None:
        lower = text.lower()
        if any(kw in lower for kw in _DELETE_KEYWORDS):
            return "delete"
        if any(kw in lower for kw in _EXPORT_KEYWORDS):
            return "export"
        return None

    def matches(self, context: SkillContext) -> bool:
        return self._intent_for(context.message_text) is not None

    def handle(self, context: SkillContext) -> SkillResult:
        intent = self._intent_for(context.message_text)
        emit(
            SKILL_DISPATCHED,
            distinct_id=str(context.bot_user.id),
            dialog_id=context.conversation.id,
            properties={"skill": self.name, "intent": intent or ""},
        )

        if intent == "delete":
            count = data_delete(context.bot_user)
            return SkillResult(
                reply_text=(f"Все ваши данные удалены. Удалено диалогов: {count}. Спасибо!"),
                should_close_conversation=True,
                meta={"skill": self.name, "intent": "delete", "conversations": count},
            )

        if intent == "export":
            archive = data_export(context.bot_user)
            # Inline the JSON in the reply — Sprint 3 ships JSON only,
            # Phase 1 will host the archive at a URL with TTL.
            archive_text = json.dumps(archive, ensure_ascii=False, indent=2)
            return SkillResult(
                reply_text=("Ваши данные в формате JSON:\n\n" + archive_text),
                meta={
                    "skill": self.name,
                    "intent": "export",
                    "conversations": len(archive["conversations"]),
                },
            )

        # Shouldn't reach here — matches() gated us. Defensive fallback.
        logger.warning("privacy_consent.handle.no_intent text=%s", context.message_text[:80])
        return SkillResult(reply_text="", should_send=False)
