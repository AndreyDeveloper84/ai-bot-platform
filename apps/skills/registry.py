"""Skill registry + dispatcher (DRF-468 / Sprint 3 / D1).

Module-global ordered list of registered skill instances. Order is
matching priority — first ``matches()`` returning True wins. The
echo fallback registers last (lowest priority) so legitimate skills
get first refusal.

### Registration

Decorate a skill class at module load with :func:`register`:

    @register
    class EchoSkill:
        name = "echo"
        def matches(self, ctx): return True
        def handle(self, ctx): return SkillResult(reply_text=ctx.message_text)

The decorator instantiates the class and appends to ``_skills``.
``SkillsConfig.ready()`` triggers imports of every skill module so
decorators fire on Django boot.

### Dispatch

:func:`dispatch` iterates ``_skills`` in order, calls ``matches()``
on each, returns the first ``handle()`` result. Returns None when no
skill matches — caller decides what to do (channel handlers fall back
to ``_echo_text`` for the legacy Sprint-2 behavior, even though the
echo skill itself is the catch-all in registered order).

### Test isolation

Skill registration is per-process module state. Tests that swap the
registry use :func:`registered` for the read side and
:func:`reset_registry_cache` to drop overrides between tests.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.skills.base import Skill, SkillContext, SkillResult

logger = logging.getLogger(__name__)

_skills: list["Skill"] = []


def register(skill_cls: type["Skill"]) -> type["Skill"]:
    """Decorator: instantiate `skill_cls` and append to the registry.

    Returns the class unchanged so the decoration is transparent to
    type-checkers and isinstance().
    """

    instance = skill_cls()
    _skills.append(instance)
    logger.debug(
        "skills.register name=%s class=%s",
        getattr(instance, "name", "?"),
        skill_cls.__name__,
    )
    return skill_cls


def registered() -> list["Skill"]:
    """Read-only snapshot of the current registry (copy)."""

    return list(_skills)


def reset_registry_cache() -> None:
    """Test hook — drop every registered skill.

    Production code should never call this. Used by skills tests that
    want to verify dispatch order against a controlled fixture set.
    """

    _skills.clear()


def dispatch(context: "SkillContext") -> "SkillResult | None":
    """Return the first matching skill's result, or None if no match.

    HUMAN_HANDOFF guard (Sprint 3 / D3): when the conversation is
    currently mid-handoff, the bot stays silent. We return a SkillResult
    with ``should_send=False`` (and an empty reply) so the channel
    handler's outbound path short-circuits cleanly. The operator drives
    the conversation through the admin until they call
    ``resolve_admin_task`` (which flips state back to IDLE).

    Otherwise: iterates skills in registration order. The first
    ``matches()`` returning True wins; ``handle()`` is called once.
    Exceptions from a skill's ``handle()`` bubble — telemetry hooks
    at the caller side decide whether to swallow.
    """

    # Late import to dodge cycles — registry sits near the root of
    # the skills graph, so model-import-on-import would propagate
    # widely.
    from apps.conversations.models import Conversation
    from apps.skills.base import SkillResult

    if context.conversation.state == Conversation.State.HUMAN_HANDOFF:
        logger.info(
            "skills.dispatch.silenced_by_handoff conversation=%s",
            context.conversation.id,
        )
        return SkillResult(
            reply_text="",
            should_send=False,
            meta={"silenced_by": "human_handoff"},
        )

    for skill in _skills:
        try:
            if skill.matches(context):
                logger.info("skills.dispatch.match name=%s", skill.name)
                result = skill.handle(context)
                # E2E-BOT-02A observability — proves which skill ran,
                # which action it produced and which tools fired, so a
                # read-only turn (e.g. show_my_bookings) is auditable
                # as mutation-free from logs alone.
                logger.info(
                    "skills.dispatch.result name=%s action_type=%s tools=%s",
                    skill.name,
                    result.action_type,
                    [call.name for call in result.tool_calls_made],
                )
                return result
        except Exception:  # noqa: BLE001 — log + rethrow
            logger.exception(
                "skills.dispatch.match_failed name=%s",
                getattr(skill, "name", "?"),
            )
            raise
    return None
