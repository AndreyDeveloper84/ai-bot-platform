"""Menu / honest-fallback skill (DRF-963 / Wave 1, variant A).

:class:`MenuSkill` is the last responder for TEXT turns before the echo
catch-all, with three jobs. Its sibling
:class:`apps.skills.menu.help_skill.HelpSkill` registers much earlier
(before faq) and owns the «что ты умеешь» vocabulary.

1. **Main-menu taps** (``cb:menu:*``) — translate the tapped slug into the
   canonical phrase an existing skill already claims and re-dispatch, so a
   button and the equivalent typed message take the identical route.
2. **U-1 — widened booking coverage** — a turn that names a service
   («Хочу массаж», «Мне бы маникюр») or asks about availability («есть
   окошко») is re-dispatched with an explicit booking
   :class:`~apps.orchestrator.intent_router.IntentDecision` so the booking
   skill claims it through its documented intent gate.
3. **U-5 — honest fallback** — anything still unrecognised gets «Я пока не
   понял…» plus the main menu, never a verbatim echo.

### Why re-dispatch instead of extending the booking matcher

DRF-963 must not touch ``apps/skills/booking/`` (S1 anti-touch), and the
booking vocabulary lives there. ``SkillContext.intent`` is the contract
built exactly for this: per :mod:`apps.skills.base`, «Sprint 4+ adds the
classifier without changing the skill protocol — skills stay agnostic to
how they were selected». :meth:`BookingSkill.matches` already honours it
(``intent.intent == "booking"``). So we classify, set the intent and let
the registry route — booking's own code is untouched and its logs, events
and handoff paths stay authoritative.

This is NOT the LLM orchestrator (variant B, explicitly out of scope for
the pilot): the classification is deterministic keyword matching and the
:class:`IntentDecision` is built locally. No LLM call is added by this
skill; the booking skill's own Phase-1 call is the same one it always made.

### Why registration order makes this zero-regression

``SkillsConfig.ready()`` registers this skill LAST before echo. Every turn
it sees is a turn that no other skill claimed — i.e. a turn that used to be
echoed. It can therefore only ADD routing, never take a turn away from
booking, FAQ, health_screening or any wellness skill. The booking channel
gates CG-1..CG-8 exercise flows that all match upstream skills, so they
cannot reach this code path.

### Recursion safety

:meth:`matches` returns False whenever ``context.intent`` is already set.
The re-dispatch always sets an intent, so the second pass walks the registry
without this skill in play and can never loop back. When the re-dispatch
finds no owner (defensive — a registry without the booking skill, as in
isolated unit tests) we fall back to the honest menu reply.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import ClassVar

from apps.skills.base import SkillContext, SkillResult
from apps.skills.menu.matching import (
    CALLBACK_MENU_HELP,
    MENU_CALLBACK_PREFIX,
    MENU_CALLBACK_TEXT,
    looks_like_booking_request,
    looks_like_help_request,
    tenant_service_stems,
)

# Re-exported for callers and tests that read the pilot's user-facing copy.
from apps.skills.menu.replies import (  # noqa: F401
    FALLBACK_TEXT,
    HELP_TEXT,
    fallback_result as _fallback_result,
    help_result as _help_result,
)
from apps.skills.registry import register

logger = logging.getLogger(__name__)


def _booking_intent() -> object:
    """Deterministic booking :class:`IntentDecision` for the re-dispatch.

    Imported lazily: :mod:`apps.orchestrator.intent_router` pulls in the
    LLM provider module at import time, and this skill is imported during
    ``AppConfig.ready()``. ``confidence=1.0`` is honest here — the decision
    comes from an exact keyword match, not from a model.
    """
    from apps.orchestrator.intent_router import IntentDecision

    return IntentDecision(
        intent="booking",
        skill="booking",
        confidence=1.0,
        risk_level="low",
        reply_mode="keyboard",
        needs_tool=True,
        raw={"source": "menu_skill", "classifier": "keyword"},
    )


@register
class MenuSkill:
    """Menu taps, widened booking coverage and the honest fallback."""

    name: ClassVar[str] = "menu"

    def matches(self, context: SkillContext) -> bool:
        # Re-dispatch guard — a context that already carries an intent is
        # our own second pass (or an orchestrator-driven turn, where the
        # classifier owns routing). Either way this skill must stand down.
        if context.intent is not None:
            return False
        # Empty / attachment-only turns stay with echo: it owns the
        # «(нечем эхом)» and «?» replies and the media fallback.
        return bool((context.message_text or "").strip())

    def handle(self, context: SkillContext) -> SkillResult:
        text = (context.message_text or "").strip()

        if text.startswith(MENU_CALLBACK_PREFIX):
            return self._handle_menu_callback(context, text)

        # Defensive: HelpSkill claims these upstream, so this branch only
        # fires when the registry is partial (isolated unit tests).
        if looks_like_help_request(text):
            return _help_result()

        # The catalog read is deliberately INSIDE the cheap-signal check
        # (via the lazy callable) so an availability phrasing — or the far
        # more common unrecognised turn — never pays for a DB round-trip.
        if looks_like_booking_request(text, extra_stems=lambda: _tenant_stems(context)):
            logger.info(
                "menu.routed_to_booking conversation=%s trace=%s",
                getattr(context.conversation, "id", "?"),
                context.trace_id,
            )
            routed = _route_to_booking(context, message_text=text, explicit=False)
            if routed is not None:
                return routed

        return _fallback_result()

    def _handle_menu_callback(self, context: SkillContext, text: str) -> SkillResult:
        if text == CALLBACK_MENU_HELP:
            return _help_result()

        canonical = MENU_CALLBACK_TEXT.get(text)
        if canonical is None:
            # Unknown / stale menu slug — never echo the raw payload at
            # the customer; show the menu instead.
            logger.info("menu.unknown_callback payload=%r", text[:64])
            return _fallback_result()

        logger.info(
            "menu.callback_routed payload=%s conversation=%s",
            text,
            getattr(context.conversation, "id", "?"),
        )
        routed = _route_to_booking(context, message_text=canonical, explicit=True)
        if routed is not None:
            return routed
        logger.warning("menu.callback_route_unclaimed payload=%s", text)
        return _fallback_result()


def _tenant_of(context: SkillContext) -> object | None:
    """Tenant behind the conversation, or None when it can't be read.

    Defensive ``getattr``: the global (tenant-less) path and unit contexts
    carrying a stub conversation both legitimately have no tenant, and the
    catalog widening is optional.
    """
    return getattr(context.conversation, "tenant", None)


def _tenant_stems(context: SkillContext) -> tuple[str, ...]:
    """Catalog-derived service words for this turn's tenant."""
    return tenant_service_stems(_tenant_of(context))


def _route_to_booking(
    context: SkillContext,
    *,
    message_text: str,
    explicit: bool,
) -> SkillResult | None:
    """Hand the turn to the booking skill's own ``handle`` contract.

    ### Why we call the skill instead of re-running dispatch

    The first implementation re-entered ``registry.dispatch`` with a
    booking ``IntentDecision`` attached. That re-walked the registry FROM
    THE TOP with a REWRITTEN message — and skills registered above booking
    do not all honour ``intent``. :class:`NutritionAnketaSkill` claims any
    non-``cb:`` text while its FSM is alive and ignores intent entirely, so
    a «📅 Записаться» tap (``cb:menu:book``, which anketa declines on the
    first pass) came back as the canonical «Хочу записаться» on the second
    pass and was swallowed by an abandoned anketa — a FSM with no TTL, and
    both buttons ship on the same welcome keyboard. The booking button was
    dead until the FSM was completed.

    Calling ``handle`` directly is also what the DRF-963 brief sanctions
    («только вызывать существующие контракты»): booking's own code, its
    own events, its own handoff reasons — just without a second registry
    walk that nobody asked for. The free-text path was never at risk (a
    skill that would claim the text already claimed it on the first walk,
    since this skill registers last), but it takes the same route so there
    is one behaviour to reason about.

    The context still carries the booking ``IntentDecision``: ``handle``
    doesn't read it, but it keeps the turn's provenance honest for anything
    that inspects the context downstream.

    ### Why ``explicit`` gates the handoff

    ``should_handoff`` is not a reply — the channel turns it into an
    AdminTask and flips the conversation to ``HUMAN_HANDOFF``, which MUTES
    the bot until an operator closes the task. Booking raises it for every
    provider failure (LLM router down, YClients/Ayla unreachable).

    * ``explicit=True`` (the customer TAPPED «Записаться») — propagate.
      They asked to book, the backend is down, a human should take over.
    * ``explicit=False`` (we INFERRED a booking intent from a service
      word) — swallow it and answer with the menu. Otherwise a backend
      blip plus a matcher false positive («Устала спина», «Юридические
      лица») would mint operator tasks and permanently mute dialogues
      that, before DRF-963, cost nothing but an echo. We inferred the
      intent; we are not confident enough to spend an operator on it.
    """
    from apps.skills.registry import registered

    booking = next((skill for skill in registered() if skill.name == "booking"), None)
    if booking is None:
        # Defensive — a registry without booking (isolated unit tests).
        logger.warning("menu.booking_skill_unavailable")
        return None

    routed_context = dataclasses.replace(
        context,
        message_text=message_text,
        intent=_booking_intent(),  # type: ignore[arg-type]
    )
    result = booking.handle(routed_context)
    if result is not None and result.should_handoff and not explicit:
        logger.warning(
            "menu.speculative_handoff_suppressed reason=%s conversation=%s",
            result.handoff_reason,
            getattr(context.conversation, "id", "?"),
        )
        return _fallback_result()
    return result
