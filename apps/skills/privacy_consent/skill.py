"""PrivacyConsentSkill (DRF-469 / Sprint 3 / D2).

Surfaces 152-ФЗ / GDPR data-subject rights through the chat:

  * "удалить мои данные" / "delete my data" / "удалить меня" →
    **redirect** to the confirmed Mini App erasure flow. No mutation.
  * "выгрузить мои данные" / "export my data" →
    `data_export(bot_user)` → JSON archive surfaced in the reply.

Match keyword set is conservative on purpose — a false positive on the
delete trigger used to be expensive. Sprint 4+ adds an intent classifier
that can soften the match.

### Why the chat delete is a redirect, not a delete (DRF-956 / T-05)

Until DRF-956 a single message ("удали мои данные") ran the full
destructive cascade with **no confirmation step at all** — one typo-level
false positive wiped an account. Worse, the cascade it called
(:func:`apps.identity.services.delete_bot_user_data`) hard-deletes the
``BotUser`` row, and that row is referenced ``on_delete=PROTECT`` by
``observability.AIRequestMetric``, ``handoff.AdminTask`` and
``tenancy.StaffAssignment``. Any user who had ever triggered one AI turn
therefore hit ``ProtectedError`` — the request 500'd *after* the
"requested" audit row was written, and the user was told nothing.

Erasure needs a confirmation the chat channel cannot express (there is no
reusable two-step confirmation primitive for free-text turns, and Pilot is
not the place to invent one). The Mini App ships a two-step confirmation
sheet, so the chat trigger now routes there and states plainly that
nothing was deleted. See ``apps/identity/services/privacy.py`` for the
cascade the Mini App runs.

.. warning::

   The Mini App's confirmation for the C5 flow is **client-side only** —
   ``DELETE /api/v1/customer/me/personal-data/`` performs no server-side
   confirmation check, unlike the legacy ``POST /me/delete``, which
   validates ``DELETE_CONFIRMATION_TOKEN`` in
   ``apps.identity.services.profile``. Gating the chat closes the
   unconfirmed *chat* path; it does not by itself make the destination
   server-confirmed. Raised as a pre-pilot owner decision (it changes a
   frozen contract endpoint + its client), tracked on DRF-956.

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
from apps.skills.privacy_consent.tools import data_export
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
# Deterministic reply for the gated delete intent. States where the
# confirmed flow lives and — explicitly — that nothing was deleted here.
# Never promise an erasure this path did not perform.
_DELETE_REDIRECT_TEXT = (
    "Данные я удаляю только после подтверждения, а в чате подтвердить нельзя.\n\n"
    "Откройте в приложении «Профиль» → «Мои данные» → «Удалить данные». "
    "Там нужно подтвердить действие — после этого я удалю то, что помню о вас, "
    "ваши настройки и согласия.\n\n"
    "Сейчас я ничего не удалила."
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
            # No mutation on this path — see the module docstring. The reply
            # must not imply anything was deleted.
            return SkillResult(
                reply_text=_DELETE_REDIRECT_TEXT,
                meta={
                    "skill": self.name,
                    "intent": "delete",
                    "outcome": "redirected_to_confirmed_flow",
                },
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
        # #842 W3 CRIT-2 — `context.message_text` is raw user input.
        # Log length-only proxy instead of text body to keep Loki /
        # Datadog out of 152-ФЗ §6 scope.
        logger.warning(
            "privacy_consent.handle.no_intent text_len=%d",
            len(context.message_text or ""),
        )
        return SkillResult(reply_text="", should_send=False)
