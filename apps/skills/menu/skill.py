"""Menu / honest-fallback skill (DRF-963 / Wave 1, variant A).

:class:`MenuSkill` is the last responder for TEXT turns before the echo
catch-all, with three jobs. Its sibling
:class:`apps.skills.menu.help_skill.HelpSkill` registers much earlier
(before faq) and owns the «что ты умеешь» vocabulary.

1. **Main-menu taps** (``cb:menu:*``) — translate the tapped slug into the
   canonical phrase and hand it to the booking skill, so a button and the
   equivalent typed message take the identical route.
2. **U-1 — widened booking coverage** — a turn that names a service
   («Хочу массаж», «Мне бы маникюр») or asks about availability («есть
   окошко») is handed to the booking skill instead of being echoed.
3. **U-5 — honest fallback** — anything still unrecognised gets «Я пока не
   понял…» plus the main menu, never a verbatim echo.

### Why we call booking instead of extending its matcher

DRF-963 must not touch ``apps/skills/booking/`` (S1 anti-touch), and the
booking vocabulary lives there. So this skill classifies the turn
deterministically (keywords, no LLM) and calls booking's own ``handle``
contract — which the brief sanctions («только вызывать существующие
контракты»). Booking's code, events, audit rows and handoff reasons stay
authoritative; nothing about it is reimplemented here.

An earlier revision instead re-entered ``registry.dispatch`` with a
synthesised booking ``IntentDecision``. That re-walked the registry from
the top with a REWRITTEN message, and skills above booking don't all
honour ``intent`` — an abandoned ``nutrition_anketa`` FSM swallowed the
canonical «Хочу записаться» and killed the «Записаться» button. See
:func:`_route_to_booking` for the full account.

This is NOT the LLM orchestrator (variant B, explicitly out of scope for
the pilot). No LLM call is added by this skill; the booking skill's own
Phase-1 call is the same one it always made.

### Why registration order makes this zero-regression

``SkillsConfig.ready()`` registers this skill LAST before echo. Every turn
it sees is a turn that no other skill claimed — i.e. a turn that used to be
echoed. It can therefore only ADD routing, never take a turn away from
booking, FAQ, health_screening or any wellness skill. The booking channel
gates CG-1..CG-8 exercise flows that all match upstream skills, so they
cannot reach this code path.

### Rollback

``settings.PILOT_CONVERSATIONAL_UX`` (default ON) turns the whole surface
off without a deploy — this skill stands down, echo takes text turns back,
and the welcome keyboard reverts. Worth having because this claims 100% of
non-empty text turns on both channels.
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
    pilot_ux_enabled,
    tenant_service_stems,
)

from apps.skills.menu.replies import fallback_result as _fallback_result
from apps.skills.menu.replies import help_result as _help_result
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# Handoff reasons this skill may swallow on the INFERRED path. Deliberately
# an allowlist of transient infrastructure failures, not a blanket rule:
# booking's reason vocabulary is extensible, and a future
# legally-sensitive / payment-dispute escalation must never be silently
# eaten by a routing helper. Anything not listed here propagates.
_SUPPRESSIBLE_HANDOFF_REASONS: frozenset[str] = frozenset(
    {
        "booking_yclients_failure",
        "booking_provider_failure",
        "llm_error",
    }
)


@register
class MenuSkill:
    """Menu taps, widened booking coverage and the honest fallback."""

    name: ClassVar[str] = "menu"

    def matches(self, context: SkillContext) -> bool:
        # Rollback switch — OFF hands every text turn back to echo, which
        # is exactly the pre-DRF-963 behaviour.
        if not pilot_ux_enabled():
            return False
        # An orchestrator-driven turn carries a classifier decision; that
        # classifier owns routing, so this skill stands down.
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

    The routed context carries the user's (or the button's canonical) text
    and nothing else. An earlier revision also attached a synthesised
    booking ``IntentDecision``; it was removed once the direct call landed,
    because ``BookingSkill.handle`` never reads ``intent`` (only
    ``matches`` does) — so the field was dead weight that bought a runtime
    ``apps.skills`` → ``apps.orchestrator.intent_router`` import edge,
    which no import-boundary contract covers and which drags the LLM
    provider module toward ``AppConfig.ready()``.

    ### Why ``explicit`` gates the handoff

    ``should_handoff`` is not a reply — the channel turns it into an
    AdminTask and flips the conversation to ``HUMAN_HANDOFF``, which MUTES
    the bot until an operator closes the task. Booking raises it for every
    provider failure (LLM router down, YClients/Ayla unreachable).

    * ``explicit=True`` (the customer TAPPED «Записаться») — propagate.
      They asked to book, the backend is down, a human should take over.
    * ``explicit=False`` (we INFERRED a booking intent from a service
      word) — swallow, but ONLY for the transient-infrastructure reasons
      in :data:`_SUPPRESSIBLE_HANDOFF_REASONS`. Otherwise a backend blip
      plus a matcher false positive («Устала спина», «Юридические лица»)
      would mint operator tasks and permanently mute dialogues that,
      before DRF-963, cost nothing but an echo. Any other reason
      propagates untouched: escalation policy belongs to the pipeline's
      confidence gate, not to a routing helper, so this stays a narrow
      carve-out rather than a veto.
    """
    from apps.skills.registry import registered

    booking = next((skill for skill in registered() if skill.name == "booking"), None)
    if booking is None:
        # Defensive — a registry without booking (isolated unit tests).
        logger.warning("menu.booking_skill_unavailable")
        return None

    routed_context = dataclasses.replace(context, message_text=message_text)
    result = booking.handle(routed_context)
    if (
        result is not None
        and result.should_handoff
        and not explicit
        and result.handoff_reason in _SUPPRESSIBLE_HANDOFF_REASONS
    ):
        logger.warning(
            "menu.speculative_handoff_suppressed reason=%s conversation=%s",
            result.handoff_reason,
            getattr(context.conversation, "id", "?"),
        )
        return _fallback_result()
    return result
