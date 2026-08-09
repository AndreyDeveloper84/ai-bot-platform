"""Menu / honest-fallback skill (DRF-963 / Wave 1, variant A).

Last responder for TEXT turns before the echo catch-all. Three jobs:

1. **Main-menu taps** (``cb:menu:*``) — translate the tapped slug into the
   canonical phrase an existing skill already claims and re-dispatch, so a
   button and the equivalent typed message take the identical route.
2. **U-1 — widened booking coverage** — a turn that names a service
   («Хочу массаж», «Мне бы маникюр») or asks about availability («есть
   окошко?») is re-dispatched with an explicit booking
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
    main_menu_action_data,
    tenant_service_stems,
)
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# U-5 — the honest fallback. Says plainly that the bot did not understand,
# then shows what it CAN do. Never echoes the user's text back.
FALLBACK_TEXT = (
    "Я пока не понял 🤔\n\n"
    "Вот что я умею:\n"
    "• записать к мастеру\n"
    "• показать ваши записи\n"
    "• перенести или отменить запись\n"
    "• ответить на вопросы об услугах, ценах и адресе\n\n"
    "Выберите действие или напишите своими словами — например, «хочу массаж»."
)

# «Помощь» / «что ты умеешь» — same menu, but framed as an answer rather
# than as a miss, so the customer isn't told they were misunderstood when
# they weren't.
HELP_TEXT = (
    "Я бот салона «Формула тела». Вот что я умею:\n\n"
    "• 📅 Записаться — подберу мастера и время\n"
    "• 📋 Мои записи — покажу ваши ближайшие визиты\n"
    "• 🔄 Перенести запись — поменяю дату или время\n"
    "• ❌ Отменить запись\n"
    "• ❓ Вопросы об услугах, ценах, адресе и режиме работы\n\n"
    "Можно нажать кнопку или написать своими словами — например, "
    "«хочу массаж спины» или «когда у меня запись?».\n"
    "Если что-то срочное — напишите «оператор», и я передам вас администратору."
)


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

        # Help before booking: «помощь» carries no service word, but a
        # phrase like «помогите записаться на массаж» would satisfy both —
        # and it was already claimed upstream by the booking keyword
        # «записаться», so it never reaches here anyway.
        if looks_like_help_request(text):
            return _help_result()

        if looks_like_booking_request(
            text,
            extra_stems=tenant_service_stems(_tenant_of(context)),
        ):
            logger.info(
                "menu.routed_to_booking conversation=%s trace=%s",
                getattr(context.conversation, "id", "?"),
                context.trace_id,
            )
            routed = _redispatch(context, message_text=text)
            if routed is not None:
                return routed
            logger.warning("menu.booking_redispatch_unclaimed — falling back to menu")

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
        routed = _redispatch(context, message_text=canonical)
        if routed is not None:
            return routed
        logger.warning("menu.callback_redispatch_unclaimed payload=%s", text)
        return _fallback_result()


def _tenant_of(context: SkillContext) -> object | None:
    """Tenant behind the conversation, or None when it can't be read.

    Defensive ``getattr``: the global (tenant-less) path and unit contexts
    carrying a stub conversation both legitimately have no tenant, and the
    catalog widening is optional.
    """
    return getattr(context.conversation, "tenant", None)


def _redispatch(context: SkillContext, *, message_text: str) -> SkillResult | None:
    """Re-run registry dispatch with a booking intent attached.

    ``SkillContext`` is frozen, so we build a copy rather than mutate. The
    copy carries ``intent`` set, which both keeps :meth:`MenuSkill.matches`
    out of the second walk (no recursion) and lets
    :meth:`BookingSkill.matches` claim the turn through its intent gate.
    """
    from apps.skills.registry import dispatch

    routed_context = dataclasses.replace(
        context,
        message_text=message_text,
        intent=_booking_intent(),  # type: ignore[arg-type]
    )
    return dispatch(routed_context)


def _fallback_result() -> SkillResult:
    return SkillResult(
        reply_text=FALLBACK_TEXT,
        action_type="menu_fallback",
        action_data=main_menu_action_data(),
        meta={"reply_kind": "menu_fallback"},
        confidence=None,
    )


def _help_result() -> SkillResult:
    return SkillResult(
        reply_text=HELP_TEXT,
        action_type="menu_help",
        action_data=main_menu_action_data(),
        meta={"reply_kind": "menu_help"},
        confidence=None,
    )
