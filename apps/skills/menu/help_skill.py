"""Help skill — the bot's capability list (DRF-963 / Wave 1, variant A).

Registered BEFORE the FAQ skill, deliberately. FAQ's keyword fallback
claims anything question-shaped (``_QUESTION_SIGNALS`` includes a bare
«?»), so «что ты умеешь?» used to become a knowledge-base lookup: two LLM
calls to answer a question about the bot itself, and — when the LLM is
unavailable — an operator handoff. A deterministic answer is cheaper,
faster and more correct.

The override is kept surgical by
:func:`apps.skills.menu.matching.looks_like_help_request`, which matches
the WHOLE normalised message against a closed phrase set. «Помогите
подобрать массаж» still reaches booking; a genuine salon question still
reaches FAQ.

Lives in its own module because ``@register`` fires on import and
:class:`~apps.skills.menu.skill.MenuSkill` must register much later (last
before echo) — see :mod:`apps.skills.menu.replies`.
"""

from __future__ import annotations

from typing import ClassVar

from apps.skills.base import SkillContext, SkillResult
from apps.skills.menu.matching import CALLBACK_MENU_HELP, looks_like_help_request
from apps.skills.menu.replies import help_result
from apps.skills.registry import register


@register
class HelpSkill:
    """Answers «помощь» / «что ты умеешь» / «меню» and the ❓ menu button."""

    name: ClassVar[str] = "help"

    def matches(self, context: SkillContext) -> bool:
        if context.intent is not None:
            # Orchestrator-driven turn (or a menu re-dispatch) — the
            # classifier owns routing.
            return False
        text = (context.message_text or "").strip()
        return text == CALLBACK_MENU_HELP or looks_like_help_request(text)

    def handle(self, context: SkillContext) -> SkillResult:
        return help_result()
