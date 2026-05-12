"""FAQ skill stub (DRF-544 / Sprint 6 / I1).

Phase 0 placeholder so the orchestrator pipeline (O-track) has a real
non-echo skill to dispatch through during the Sprint 6 exit-gate. The
Sprint 7 KB integration (F0.14) swaps this for a knowledge-base-backed
implementation that uses `search_knowledge_base` tool.

### Matching rule (Sprint 6 stub)

Keyword-based: trips when the message contains a question signal —
'?', 'сколько', 'когда', 'где', 'почему', 'какие', plus their English
equivalents. Conservative on purpose: false positives route messages
to a canned reply that goes nowhere useful; better to defer to echo
when the FAQ skill is not confident.

### Response shape

Phase 0: single canned reply. Phase 1+: KB-driven answer with citations.
The shape (`SkillResult.reply_text` + `meta`) is forward-compatible —
Sprint 7 changes the WHERE the text comes from, not the WHAT.
"""

from __future__ import annotations

from typing import ClassVar

from apps.events.services import emit
from apps.events.vocabulary import SKILL_DISPATCHED
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

_QUESTION_SIGNALS: tuple[str, ...] = (
    "?",
    "сколько",
    "когда",
    "где",
    "почему",
    "как ",  # trailing space — avoid matching "какой/какие" twice
    "какой",
    "какая",
    "какие",
    "что такое",
    "what is",
    "how much",
    "when",
    "where",
    "why",
)

_FAQ_REPLY = (
    "Спасибо за вопрос! Я уточню детали и пришлю развёрнутый ответ. "
    "Если нужно сразу — напишите «оператор», и я свяжу вас с менеджером."
)


@register
class FAQSkill:
    """Trigger: user asks a question (keyword heuristic).

    Sprint 6 stub. Sprint 7 lands KB-backed real answers (F0.14).
    Registered BEFORE echo so the catch-all only fires when this
    skill (and privacy/handoff) decline.
    """

    name: ClassVar[str] = "faq"

    @staticmethod
    def _matches_signal(text: str) -> bool:
        lower = text.lower()
        return any(signal in lower for signal in _QUESTION_SIGNALS)

    def matches(self, context: SkillContext) -> bool:
        return self._matches_signal(context.message_text)

    def handle(self, context: SkillContext) -> SkillResult:
        emit(
            SKILL_DISPATCHED,
            distinct_id=str(context.bot_user.id),
            dialog_id=context.conversation.id,
            properties={"skill": self.name, "stub": True},
        )
        return SkillResult(
            reply_text=_FAQ_REPLY,
            meta={"skill": self.name, "stub": True},
        )
