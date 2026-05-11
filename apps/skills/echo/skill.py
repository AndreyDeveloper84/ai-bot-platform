"""Echo fallback skill (DRF-468 / Sprint 3 / D1).

Last-resort responder for messages no other skill claims. Reproduces
the Sprint 2 echo behavior so the channel pipeline keeps its existing
end-to-end semantics while skill dispatch is being wired.

Behavior:
  * ``/start`` → welcome text
  * non-empty text → verbatim echo
  * empty text + attachments → "no echo"
  * empty text + no attachments → "?"
"""

from __future__ import annotations

from typing import ClassVar

from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

# Re-use the Sprint 2 strings verbatim so the end-to-end byte-for-byte
# outbound stays identical while we add skill dispatch. The channel
# handler still owns its own outbound-emit "reply_kind" branching that
# compares against `_WELCOME_TEXT`, so co-location avoids drift.
from apps.channels.max.handler import (
    _FALLBACK_EMPTY,
    _FALLBACK_NO_ECHO,  # noqa: F401 — kept for parity / future media skill
    _WELCOME_TEXT,
)


@register
class EchoSkill:
    """Catch-all skill. Always matches; lowest priority by registration order."""

    name: ClassVar[str] = "echo"

    def matches(self, context: SkillContext) -> bool:
        # Always — the order of registration makes this last-resort.
        return True

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        if text == "/start":
            return SkillResult(reply_text=_WELCOME_TEXT, meta={"reply_kind": "welcome"})
        if text:
            return SkillResult(reply_text=context.message_text, meta={"reply_kind": "echo"})
        if context.has_attachments:
            return SkillResult(reply_text=_FALLBACK_NO_ECHO, meta={"reply_kind": "no_echo"})
        return SkillResult(reply_text=_FALLBACK_EMPTY, meta={"reply_kind": "empty_prompt"})
