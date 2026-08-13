"""HumanHandoffSkill (DRF-470 / Sprint 3 / D3).

Trigger: user asks for a human operator. The skill creates an
``AdminTask`` via :func:`apps.handoff.services.create_admin_task`
(which atomically flips ``Conversation.state`` → HUMAN_HANDOFF and
emits the canonical ``handoff_initiated`` event) and confirms to the
user that an operator is on the way.

### Dispatcher guard

D1's :func:`apps.skills.registry.dispatch` is upgraded in this task
(D3) with a state guard: when the conversation is currently in
HUMAN_HANDOFF, ``dispatch()`` returns a "silent" SkillResult
(``should_send=False``) instead of running any skill. The bot stays
quiet until the operator resolves the handoff via
``resolve_admin_task`` (C2) which flips state back to IDLE.

This is enforced at the dispatcher layer rather than per-skill so
every Sprint 4+ skill inherits the guard for free.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from apps.handoff.models import AdminTask
from apps.handoff.services import create_admin_task
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)

_HANDOFF_KEYWORDS: tuple[str, ...] = (
    "оператор",
    "связь с человеком",
    "поддержка живой",
    "живая поддержка",
    "менеджер",
    "администратор",
    "админ",
    "human",
    "agent",
    "manager",
)

_HANDOFF_REPLY = "Передаю менеджеру — ответят в течение 30 минут."


@register
class HumanHandoffSkill:
    """Trigger: user explicitly asks for a human operator."""

    name: ClassVar[str] = "human_handoff"

    def matches(self, context: SkillContext) -> bool:
        lower = context.message_text.lower()
        return any(kw in lower for kw in _HANDOFF_KEYWORDS)

    def handle(self, context: SkillContext) -> SkillResult:
        reason = f"Trigger phrase: {context.message_text[:80]}"
        # create_admin_task atomically:
        #   - inserts AdminTask with the transcript snapshot
        #   - flips Conversation.state IDLE → HUMAN_HANDOFF
        #   - emits the canonical `handoff_initiated` event
        #   - writes the `handoff.created` audit row
        task = create_admin_task(
            context.conversation,
            task_type=AdminTask.TaskType.HANDOFF,
            reason=reason,
        )
        logger.info(
            "human_handoff.created task=%s conversation=%s",
            task.id,
            context.conversation.id,
        )
        return SkillResult(
            reply_text=_HANDOFF_REPLY,
            new_state="human_handoff",
            meta={
                "skill": self.name,
                "task_id": str(task.id),
                "task_type": AdminTask.TaskType.HANDOFF,
            },
        )
