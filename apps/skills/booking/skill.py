"""Booking skill — 4-tool LLM-use flow (DRF-839 / Phase 1 / B3).

Two-call LLM loop, mirroring :class:`apps.skills.faq.skill.FAQSkill`:

1. **First LLM call** with all 4 booking tools registered. The model
   either replies directly (small-talk / clarifying question) or emits
   exactly one tool call.
2. **Tool dispatch** — execute the chosen handler with the YClients
   client. The handler returns a :class:`BookingToolResult` carrying
   the tool output + the master/slot allow-sets the next call may
   reference.
3. **Second LLM call** with the tool payload spliced back into the
   system prompt → produces the natural-language reply.

### Anti-hallucination

The booking-tools handlers validate ``master_id`` and ``service_id``
against pre-fetched allow-sets. A hallucinated ID short-circuits to
``should_handoff=True`` with ``handoff_reason="booking_invalid_master_id"``
(or ``..._service_id``). The system prompt also tells the model to
never invent IDs — that's the first line of defence; this validator
is the second.

### Health-check gate

A master×service pair Ayla marks as needing a health check can't be
booked through the bot without a human in the loop. The skill resolves
the verdict BEFORE the confirm call and, if the gate fires, returns
``should_handoff=True`` with reason ``booking_health_check_required``.

The source depends on the path (see ``_service_requires_health_check``):
the legacy YClients path reads ``CatalogService.requires_health_check``;
the Ayla REST path reads the RESOLVED per-edge
``MasterService.resolved_requires_health_check`` (DRF-1353), mirrored
from Ayla's escalate-only OR of template floor → salon service →
specialist. Unknown → gate closed.

Scope, stated plainly: this is the conversational channel's routing
policy, not a platform-wide interlock. No other booking entry point
reads the flag.

### Deterministic callback short-circuits

Booking-flow button taps (``cb:book:pick_master:`` /
``cb:book:pick_date:`` / ``cb:book:pick_slot:``) bypass the Phase-1 LLM
entirely. Their payloads are self-contained (ids + date/slot), so
``handle()`` validates them against live tenant data and renders the
next step — date picker, slot cards, or the confirm preview —
deterministically. pick_slot additionally re-checks slot availability
and reuses an identical active pending on duplicate taps (RB1.1-D05).

### Handoff reasons (locked vocabulary)

* ``booking_no_masters`` — show_masters returned empty.
* ``booking_yclients_failure`` — any YClients failure path.
* ``booking_provider_failure`` — LLM router lookup failed (missing API
  key, GrowthBook flag off, circuit-broken). Same friendly handoff text
  as the YClients path so the customer never sees a raw 500.
* ``booking_invalid_master_id`` — LLM hallucinated.
* ``booking_invalid_service_id`` — LLM hallucinated.
* ``booking_health_check_required`` — gated service.
* ``booking_unknown_tool`` — LLM emitted a tool we don't expose.

### Async-from-sync bridge

Same pattern as FAQ: ``handle()`` is sync, two ``asyncio.run`` calls
wrap the async provider methods, ORM writes (BookingRequest /
BookingReminder) happen in the outer sync scope so Django's "no sync
ORM in async context" guard doesn't fire.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Set as AbstractSet
from datetime import datetime, timedelta
from typing import Any, ClassVar

from django.utils import timezone

from apps.audit.services import write_audit
from apps.booking.models import PendingBookingAction
from apps.bookings.keyboards import (
    CALLBACK_BOOK_MORE_DATES_PREFIX,
    CALLBACK_BOOK_PICK_DATE_PREFIX,
    CALLBACK_BOOK_PICK_MASTER_PREFIX,
    CALLBACK_BOOK_PICK_PART_PREFIX,
    CALLBACK_BOOK_PICK_SLOT_PREFIX,
    confirm_2_button,
)
from apps.bookings.pending_actions import (
    is_cancel_text,
    is_confirm_text,
    latest_relevant_pending,
)
from apps.events.services import emit
from apps.events.vocabulary import BOOKING_FLOW_STATE_WRITE_FAILED, SKILL_DISPATCHED
from apps.integrations.yclients import YClientsAPIError, YClientsUnavailableError
from apps.llm.protocol import CompletionResult, LLMError, ToolCall
from apps.persona.voice import DEFAULT_SALON_PERSONA
from apps.skills.base import SkillContext, SkillResult
from apps.skills.booking.lookup import (
    booking_mutation_flow,
    is_booking_request,
    is_cancel_request,
    is_personal_booking_lookup,
    looks_like_flow_selection,
)
from apps.orchestrator.time_preference import (
    PART_CHIP_LABELS,
    PART_ORDER,
    PART_PHRASES,
    PART_RANGE_HINTS,
    TimePreference,
    day_label,
    describe,
    load_time_preference,
    local_today,
    part_of_iso_datetime,
    resolve_date,
)
from apps.skills.booking.provider import YClientsScheduleUnavailableError
from apps.skills.booking.prompts import BrandVoiceConfig, build_booking_prompt
from apps.skills.booking.tools import (
    BOOKING_TOOL_SPECS,
    BUY_CERTIFICATE_TOOL_SPEC,
    CALC_PRICE_TOOL_SPEC,
    CANCEL_BOOKING_TOOL_SPEC,
    CONFIRM_BOOKING_TOOL_SPEC,
    RESCHEDULE_BOOKING_TOOL_SPEC,
    SCHEDULE_UNAVAILABLE_TEXT,
    SHOW_MASTERS_TOOL_SPEC,
    SHOW_MY_BOOKINGS_TOOL_SPEC,
    SHOW_SLOTS_TOOL_SPEC,
    BookingToolResult,
    PendingPreview,
    _booking_via_ayla,
    _coerce_id,
    _format_confirm_preview,
    _id_key,
    _to_slot_candidate,
    build_master_lookup,
    build_service_lookup,
    buy_certificate,
    calc_price,
    cancel_booking,
    confirm_booking,
    get_active_booking_tool_specs,
    reschedule_booking,
    show_masters,
    show_my_bookings,
    show_slots,
)
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# Sprint 7 hardcoded brand voice — same default as FAQ. Sprint 8
# PromptRegistry lifts this into a per-tenant config.
_DEFAULT_BRAND_VOICE = BrandVoiceConfig(
    persona=DEFAULT_SALON_PERSONA,
    tone="дружелюбный, краткий, без канцелярита",
    forbidden=(
        "гарантирую",
        "100%",
        "лучшие в городе",
        "уникальная технология",
    ),
)


_FALLBACK_HANDOFF_TEXT = "Не получилось оформить запись — переключаю на менеджера, он подскажет."

# DRF-1005 §3.3: the health-check handoff is a POLICY (the service needs a
# consultation before booking), not a failure — the generic failure text
# above would mislead the user into thinking something broke.
_HEALTH_CHECK_HANDOFF_TEXT = (
    "Для этой услуги нужна консультация — передаю менеджеру, он поможет с записью."
)

# Deterministic prompt shown when the master-cards keyboard is sent.
# Buttons carry the data — text only frames the choice. Kept short
# because MAX inline_keyboard wraps below the message body and the
# user reads the buttons, not the prose.
_MASTER_PICK_PROMPT = "Выберите мастера:"

# Same pattern for slot picking after show_slots returns candidates.
_SLOT_PICK_PROMPT = "Выберите время:"

# Date picker — shown after master pick, before slot listing.
_DATE_PICK_PROMPT = "Выберите дату:"
_DATE_PICKER_FALLBACK_NO_DATES = "У выбранного мастера нет свободных дат в ближайшее время."

# ── DRF-1325: the human-time half of the flow ─────────────────────────────
#
# The wording rule behind every line below: name what the user asked for in
# their own words before answering it. The pilot defect was not a wrong date
# — it was a request that vanished without a trace, so the person had no way
# to know it had been dropped.
#
# Nothing here claims a time is free. «Свободно» describes the schedule read
# the flow just performed; the authoritative answer is still `create` with
# its 409 (docs/OD_SALON_P0_CONTRACT.md).
_PART_PICK_PROMPT = "Когда удобно {day}?"
_PART_SLOT_PROMPT = "{day}, {part} — выберите время:"
_HEARD_SLOT_PROMPT = "Вы просили {heard} — вот что есть:"
_DAY_UNAVAILABLE_PROMPT = "На {day} у мастера свободного времени нет. Вот ближайшие дни:"
_PART_UNAVAILABLE_PROMPT = "{day} {part} у мастера свободного времени нет. Есть так:"
_PART_EMPTY_PROMPT = "Свободного времени на {part} в этот день нет. Вот весь день:"

# «Точное время» — the escape hatch out of the chips into the full list of
# the day. It must exist: chips are a shortcut for the common case, never a
# cage, and somebody who wants 15:45 specifically has to be able to get it.
_LABEL_EXACT_TIME = "Точное время"

# Same role one step earlier: out of the three day chips into every free day
# the master has.
_LABEL_PICK_DATE = "Выбрать дату"

# Number of day chips before «Выбрать дату» takes over. Three, because
# «Сегодня / Завтра / Послезавтра» is the vocabulary people actually use for
# a booking; past that a chip needs a date on it anyway, and a list of dates
# is what the full picker already is.
_MAX_DAY_CHIPS = 3

# ── Deterministic booking-callback refusals (DRF-1473) ────────────────────
#
# The guard below is right to refuse an incomplete tap; what it was wrong
# about was WHY. Every refusal on this path used to answer «Контекст записи
# устарел», whatever had actually happened, and three of the four things that
# reach it have nothing to do with time. On 04.09.2026 the owner tapped a slot
# ELEVEN SECONDS after it was drawn and was told his context had expired; the
# real cause was a specialist roster truncated at page 1 of a paginated feed
# (fixed in ``apps.integrations.ayla.booking_client.get_masters``). The lie
# cost twice: the person could not tell what to do next, and the engineer
# reading the journal was sent to look at expiry windows.
#
# So the refusals are split by cause, in the text AND in the log, and each
# text says only what is true of its own branch:
#
# * ``_STALE_CONTEXT_TEXT`` — something genuinely ran out of time. The ONLY
#   branch that may say «устарел»: a tapped slot that is now in the past.
# * ``_BROKEN_CALLBACK_TEXT`` — the payload did not carry what the step
#   needs (no service id, unparsable id, malformed datetime). Nothing
#   expired; the button itself is unusable.
# * ``_CONTEXT_GONE_TEXT`` — the payload parsed fine, but the master or the
#   service it names is not in the tenant's live data. Either the salon
#   changed under the keyboard, or — the pilot's case — the flow could not
#   see the whole roster.
#
# Every one of them exits through :func:`_refuse_callback`, which is what
# guarantees the journal line and the user's line agree.
_STALE_CONTEXT_TEXT = "Контекст записи устарел. Начните выбор услуги заново."
_BROKEN_CALLBACK_TEXT = (
    "Эта кнопка пришла без части данных — не вижу, что было выбрано. "
    "Выберите услугу ещё раз, пожалуйста."
)
_CONTEXT_GONE_TEXT = (
    "Не нахожу этого мастера или эту услугу в расписании салона. Выберите услугу заново."
)
_SLOT_TAKEN_PROMPT = "Это время уже занято. Выберите другое:"
_SLOT_TAKEN_NO_ALTERNATIVES = (
    "Это время уже занято, и на эту дату свободных слотов больше нет. Выберите другую дату."
)

# How many dates to render in the picker. YClients usually returns
# a 30-day window; trimming keeps the keyboard tappable on mobile.
_MAX_DATE_BUTTONS = 14

# E0#1 Variant A (founder verdict 2026-06-02) — cap on pre-injected
# master roster size. Pilot salons have 5-15 masters; larger tenants
# get top-N + ordered fallback so the system prompt token budget stays
# bounded. The remaining masters are still reachable via the standard
# `show_masters` tool call path — pre-injection is the first-line
# anti-hallucination defence, not the only resolver.
_KNOWN_MASTERS_ROSTER_CAP = 20


# D-10 — booking-flow continuation state. Written to
# ``Conversation.skill_state[_FLOW_STATE_KEY]`` when a mutation-request
# turn («Перенеси мою запись») ends with a bookings listing instead of
# a tool preview (the disambiguation question). While fresh, the skill
# claims follow-up turns and grounds the Phase-1 LLM call with the
# stored bookings + current local time so a single call can resolve
# «Первую, на 9 августа в 20:00» → reschedule_booking(record_id, ISO).
# Same 10-minute TTL as the PendingBookingAction preview window.
_FLOW_STATE_KEY = "booking_flow"
_FLOW_STATE_TTL = timedelta(minutes=10)
_FLOW_STAGE_AWAITING_SELECTION = "awaiting_selection"

_FLOW_ABORT_REPLIES = {
    "reschedule": "Хорошо, не переношу запись. Если передумаете — напишите.",
    "cancel": "Хорошо, не отменяю запись. Если передумаете — напишите.",
}


# Audit / event slugs.
EVENT_BOOKING_HANDLED = "booking.handled"
EVENT_BOOKING_HANDOFF = "booking.handoff"
# DRF-1005: owner-required trace for every evaluation where the pilot
# allowlist disabled the health-check gate for a tenant.
EVENT_BOOKING_HEALTH_GATE_DISABLED = "booking.health_check_gate_disabled"


# Confidence values — booking is mostly deterministic so we report a
# binary signal: ``1.0`` when a tool ran cleanly, ``None`` when no tool
# was needed (small talk), and we set ``should_handoff`` instead of a
# low confidence score for error paths (cleaner for the O2 pipeline).
_CONFIDENCE_OK = 1.0


# Booking keyword fallback — only fires when ``ctx.intent`` is absent
# (tests, Sprint-3 callers). The intent classifier owns booking
# detection in production.
_BOOKING_KEYWORDS: tuple[str, ...] = (
    "запиши",
    "записаться",
    "запись",
    "забронир",
    "хочу на",
    # B5 / DRF-841 — customer-initiated cancel + reschedule keywords.
    # These nudge the keyword fallback into the booking skill when the
    # intent classifier isn't around (tests, legacy callers). The LLM
    # tool selection still owns the actual cancel-vs-reschedule pick.
    "отмени",
    "отменить",
    "перенес",
    "перенести",
    # B6 / DRF-842 — price / promo questions route into the booking
    # skill so calc_price is reachable from the keyword fallback path.
    "сколько стоит",
    "сколько будет",
    "цена",
    "промокод",
    # B7 / DRF-843 — buy_certificate keyword fallback.
    "сертификат",
    "подарочн",
)


@register
class BookingSkill:
    """4-tool booking flow.

    Two LLM calls + one tool dispatch + idempotent ORM write +
    handoff-on-error.
    """

    name: ClassVar[str] = "booking"

    def matches(self, context: SkillContext) -> bool:
        # Booking-flow callback taps (2026-05-21 UX). User tapped a
        # button from a prior booking reply; the tapped button's payload
        # carries the deterministic prefix. Take these before the intent
        # classifier — the prefixes are unambiguous and the classifier
        # might mis-route a bare numeric string / ISO datetime.
        #   * cb:book:pick_master:<staff_id>:<service_id>              — master cards (#505)
        #   * cb:book:pick_date:<master_id>:<date>:<service_id>        — date picker (#517 fixup)
        #   * cb:book:pick_slot:<master_id>:<service_id>:<iso_datetime> — slot cards (#513)
        text = (context.message_text or "").strip()
        if text.startswith(CALLBACK_BOOK_PICK_MASTER_PREFIX):
            return True
        if text.startswith(CALLBACK_BOOK_PICK_DATE_PREFIX):
            return True
        if text.startswith(CALLBACK_BOOK_PICK_SLOT_PREFIX):
            return True
        # DRF-1325 — the time chips are booking callbacks like any other.
        if text.startswith(CALLBACK_BOOK_PICK_PART_PREFIX):
            return True
        if text.startswith(CALLBACK_BOOK_MORE_DATES_PREFIX):
            return True

        intent = context.intent
        if intent is not None:
            return intent.intent == "booking"

        # D-10 — active booking-flow continuation claims the turn BEFORE
        # the keyword fallback: «Первую, на 9 августа в 20:00» carries no
        # booking keyword, but a fresh skill_state["booking_flow"] marks
        # it as the disambiguation answer. Ownership is deliberately
        # narrow (review finding #2 — the original unrestricted claim
        # swallowed ANY text for the full 10-minute TTL, so «спасибо»
        # or a fresh «Хочу маникюр» were mis-routed into the flow at
        # two LLM calls per turn). The claim ADDS matches; a non-claim
        # falls through to the normal fallbacks below (review round 2 —
        # an unconditional ``return looks_like_flow_selection(...)``
        # shadowed is_personal_booking_lookup/_legacy_keyword_match for
        # the whole TTL and echo'ed fresh booking requests):
        #   * cb:* texts belong to their dedicated callback skills;
        #   * confirm/cancel vocab is claimed only when NO relevant
        #     PendingBookingAction exists — with one the gate skill
        #     (registered right after this one) owns the turn. Without
        #     one: cancel → the deterministic flow-abort in handle(),
        #     confirm → a Phase-1 replay with flow grounding (review
        #     round 2 — the Phase-1-without-tool-call degradation of
        #     the D-10 root cause ends in a free-text «Подтверждаете?»
        #     with NO pending row; «да» must re-enter the flow, not
        #     echo);
        #   * any other text is claimed only when it looks like a
        #     selection answer (ordinal / date / time / bare number);
        #     otherwise it falls through to the standard fallbacks.
        if not text.startswith("cb:"):
            flow_state = _read_flow_state(context.conversation)
            if flow_state is not None:
                if is_confirm_text(text) or is_cancel_text(text):
                    if (
                        latest_relevant_pending(
                            tenant=context.conversation.tenant,
                            bot_user=context.bot_user,
                        )
                        is None
                    ):
                        return True
                elif looks_like_flow_selection(text):
                    return True

        # E2E-BOT-02A — personal booking lookups ("покажи мои записи",
        # "на когда я записан?") don't always contain the literal
        # "запись" keyword; claim them explicitly on the no-intent
        # fallback path (production webhook dispatch sets no intent).
        if is_personal_booking_lookup(context.message_text):
            return True

        # DRF-1060 / OD-IR1 — natural cancellation phrasings («не приду»,
        # «не смогу прийти», «снимите меня», «передумала») carry none of
        # the _BOOKING_KEYWORDS cancel verbs, so the keyword fallback
        # below never claimed them: the turn fell through to the menu
        # fallback, the person believed they had cancelled, and the visit
        # stayed `confirmed` until it became a no-show (DRF-1048). The
        # predicate is deliberately stricter than the lookup one — it
        # feeds a mutation path — and rejects reschedule-shaped turns
        # («не смогу в среду, можно в четверг?»), so this claim cannot
        # turn a move into a drop.
        if is_cancel_request(context.message_text):
            return True

        # DRF-981 — the booking request phrased as a question. FAQ yields
        # it (``apps.skills.faq.skill.FaqSkill.matches``) reading the
        # SAME predicate, so the turn it steps out of cannot fall
        # through to the menu fallback. Two of these phrasings
        # («Можно записаться на маникюр?») already carry a
        # _BOOKING_KEYWORDS root and would be claimed by the line below
        # — they never reached it, because FAQ is registered first.
        if is_booking_request(context.message_text):
            return True

        return _legacy_keyword_match(context.message_text)

    def handle(self, context: SkillContext) -> SkillResult:
        from apps.llm.router import get_router
        from apps.skills.booking.provider import get_booking_provider

        emit(
            SKILL_DISPATCHED,
            distinct_id=str(context.bot_user.id),
            dialog_id=context.conversation.id,
            properties={"skill": self.name},
        )

        tenant = context.conversation.tenant
        tenant_id = str(tenant.id)
        # Skills retro residual #8: wrap the LLM router lookup in the same
        # fail-soft envelope as the YClients prefetch below. A misconfigured
        # provider (missing API key, circuit-broken, GrowthBook flag off)
        # used to surface as a raw 500 to the customer; now they get the
        # same friendly handoff text + audit row as a YClients outage.
        try:
            provider = get_router().get_provider(tenant, skill=self.name, op="complete")
        except Exception as exc:  # noqa: BLE001
            logger.warning("booking.provider.lookup_failed err=%s", exc)
            return _handoff(
                tool_calls_made=[],
                reason="booking_provider_failure",
                text=_FALLBACK_HANDOFF_TEXT,
                tenant_id=tenant_id,
            )
        model = getattr(provider, "default_completion_model", None) or ""

        # Pre-fetch service catalog up front — used for service_id
        # validation in confirm_booking AND for the health-check gate.
        # Failure is fatal because we can't validate IDs without it.
        try:
            # Provider selection (S1 / #1016): YClients (default) or the Ayla
            # canonical REST bridge behind ``BOOKING_VIA_AYLA_REST``. The
            # adapter presents the YClients-shaped interface the tools expect,
            # so the rest of this flow is provider-agnostic.
            yclients = get_booking_provider(bot_user=context.bot_user)
            services = yclients.get_services()
        except YClientsScheduleUnavailableError as exc:
            # DRF-997: transient 429/outage on the service catalog is NOT a
            # manager handoff. Surface the deterministic retry message so the
            # user can try again in a moment.
            logger.warning("booking.prefetch.schedule_unavailable err=%s", exc)
            return _build_skill_result(
                text=SCHEDULE_UNAVAILABLE_TEXT,
                tool_calls_made=[],
                confidence=_CONFIDENCE_OK,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("booking.prefetch.failed err=%s", exc)
            return _handoff(
                tool_calls_made=[],
                reason="booking_yclients_failure",
                text=_FALLBACK_HANDOFF_TEXT,
                tenant_id=tenant_id,
            )

        allowed_service_ids = {_id_key(s.id) for s in services}
        service_lookup = build_service_lookup(services)

        # ── Master-pick callback short-circuit ──────────────────────
        # User tapped a master-card button (cb:book:pick_master:<id>:<service_id>).
        # Fetch the master's available dates and render them as a date
        # picker. show_slots is NOT dispatched here — it would auto-pick
        # the nearest date and surprise the user (UX feedback 2026-05-21:
        # "меня не спросили про дату"). Date-pick callback fires
        # show_slots(master_id, date_from=<date>, service_id=<id>) on the
        # user's choice.
        text = (context.message_text or "").strip()
        # DRF-1325 — set by the part-of-day chip; narrows the slot list the
        # slot-cards short-circuit renders further down. None = «all times»,
        # which is what every pre-DRF-1325 path produced.
        part_filter: str | None = None
        if text.startswith(CALLBACK_BOOK_PICK_MASTER_PREFIX):
            raw_payload = text[len(CALLBACK_BOOK_PICK_MASTER_PREFIX) :].strip()
            parts = raw_payload.split(":", 1)
            master_id = _coerce_id(parts[0]) if parts else None
            service_id = _coerce_id(parts[1]) if len(parts) > 1 else None
            if master_id is None:
                logger.warning("booking.pick_master.bad_id raw=%r", raw_payload)
                return _build_skill_result(
                    text="Не удалось распознать выбор мастера. Напишите имя ещё раз?",
                    tool_calls_made=[],
                    confidence=None,
                )
            if service_id is None:
                return _refuse_callback(
                    step="pick_master",
                    reason=REFUSAL_MALFORMED_CALLBACK,
                    text=_BROKEN_CALLBACK_TEXT,
                    detail=f"field=service_id raw={raw_payload!r}",
                )
            return _render_date_picker(
                master_id=master_id,
                service_id=service_id,
                yclients=yclients,
                tenant_id=tenant_id,
                tenant=tenant,
                pref=load_time_preference(context.conversation),
            )

        # ── «Выбрать дату» — expand the day chips (DRF-1325) ────────
        # The three chips answer «когда?» for the overwhelming majority
        # of taps; this is the escape hatch for everybody else, and it
        # renders exactly what the picker rendered before this ticket.
        if text.startswith(CALLBACK_BOOK_MORE_DATES_PREFIX):
            payload = text[len(CALLBACK_BOOK_MORE_DATES_PREFIX) :].strip()
            raw_master, _, raw_service = payload.partition(":")
            master_id = _coerce_id(raw_master)
            service_id = _coerce_id(raw_service)
            if master_id is None or service_id is None:
                return _refuse_callback(
                    step="more_dates",
                    reason=REFUSAL_MALFORMED_CALLBACK,
                    text=_BROKEN_CALLBACK_TEXT,
                    detail=f"raw={payload!r}",
                )
            return _render_date_picker(
                master_id=master_id,
                service_id=service_id,
                yclients=yclients,
                tenant_id=tenant_id,
                tenant=tenant,
                pref=None,
                expand_all=True,
            )

        # ── Slot-pick callback short-circuit (RB1.1-D05) ───────────
        # Fully deterministic: parse → validate context against live
        # tenant data → re-check slot availability → confirm preview +
        # PendingBookingAction. No Phase-1 LLM: the stateless tool-choice
        # prompt refused to ground the synthesised UUID query and looped
        # back to show_masters (Wave 1 RB1.1 live gate evidence), so the
        # create flow never reached a preview.
        if text.startswith(CALLBACK_BOOK_PICK_SLOT_PREFIX):
            return _handle_pick_slot_callback(
                text=text,
                context=context,
                tenant=tenant,
                tenant_id=tenant_id,
                yclients=yclients,
                allowed_service_ids=allowed_service_ids,
                service_lookup=service_lookup,
            )

        # ── Date-pick callback short-circuit ────────────────────────
        # User tapped a day chip
        # (cb:book:pick_date:<master_id>:<date>:<service_id>).
        #
        # DRF-1325: this used to synthesise show_slots and dump every free
        # time of the day into a keyboard — the «Выберите время» half of the
        # bare calendar. Now it asks the question a person actually answers
        # («утро / день / вечер») and only then lists times. The parts are
        # derived from the day's ACTUAL slots, so a chip that appears always
        # has something behind it; when only one part has slots the question
        # is pointless and the times are shown straight away.
        if text.startswith(CALLBACK_BOOK_PICK_DATE_PREFIX):
            payload = text[len(CALLBACK_BOOK_PICK_DATE_PREFIX) :].strip()
            try:
                raw_master, raw_date, raw_service = payload.split(":", 2)
            except (TypeError, ValueError):
                logger.warning("booking.pick_date.bad_payload raw=%r", payload)
                return _build_skill_result(
                    text="Не удалось распознать дату. Попробуйте ещё раз.",
                    tool_calls_made=[],
                    confidence=None,
                )
            # Flag OFF: int YClients id; flag ON: Ayla UUID string.
            master_id = _coerce_id(raw_master)
            service_id = _coerce_id(raw_service)
            if master_id is None:
                logger.warning("booking.pick_date.bad_payload raw=%r", payload)
                return _build_skill_result(
                    text="Не удалось распознать дату. Попробуйте ещё раз.",
                    tool_calls_made=[],
                    confidence=None,
                )
            if service_id is None:
                return _refuse_callback(
                    step="pick_date",
                    reason=REFUSAL_MALFORMED_CALLBACK,
                    text=_BROKEN_CALLBACK_TEXT,
                    detail=f"field=service_id raw={payload!r}",
                )
            return _render_part_picker(
                master_id=master_id,
                service_id=service_id,
                date=raw_date,
                yclients=yclients,
                tenant_id=tenant_id,
                tenant=tenant,
                pref=load_time_preference(context.conversation),
            )

        # ── Part-of-day callback short-circuit (DRF-1325) ───────────
        # cb:book:pick_part:<master_id>:<date>:<part>:<service_id>. Exactly
        # the synth-ToolCall shape the date pick used before this ticket —
        # the ONLY difference is that ``part_filter`` narrows the rendered
        # list to the bucket the user tapped. ``any`` is «Точное время»: the
        # unfiltered list, i.e. the pre-DRF-1325 behaviour, kept reachable.
        if text.startswith(CALLBACK_BOOK_PICK_PART_PREFIX):
            payload = text[len(CALLBACK_BOOK_PICK_PART_PREFIX) :].strip()
            try:
                raw_master, raw_date, raw_part, raw_service = payload.split(":", 3)
            except (TypeError, ValueError):
                return _refuse_callback(
                    step="pick_part",
                    reason=REFUSAL_MALFORMED_CALLBACK,
                    text=_BROKEN_CALLBACK_TEXT,
                    detail=f"field=shape raw={payload!r}",
                )
            master_id = _coerce_id(raw_master)
            service_id = _coerce_id(raw_service)
            if master_id is None or service_id is None:
                return _refuse_callback(
                    step="pick_part",
                    reason=REFUSAL_MALFORMED_CALLBACK,
                    text=_BROKEN_CALLBACK_TEXT,
                    detail=f"field=ids raw={payload!r}",
                )
            part_filter = raw_part if raw_part in PART_ORDER else None
            first = CompletionResult(
                text="",
                tool_calls=[
                    ToolCall(
                        id=f"synth:pick_part:{master_id}:{raw_date}:{raw_part}",
                        name=SHOW_SLOTS_TOOL_SPEC["name"],
                        arguments={
                            "master_id": master_id,
                            "date_from": raw_date,
                            "service_id": service_id,
                        },
                    )
                ],
                provider="synth",
                finish_reason="tool_calls",
            )
        # ── Personal booking-lookup fast path (E2E-BOT-02A) ─────────
        # "Когда у меня следующая запись?" / "Покажи мои записи" —
        # deterministic read-only show_my_bookings selection, same
        # synth-ToolCall pattern as the date-pick callback above.
        # Skipping the Phase-1 LLM tool choice means a lookup turn can
        # never drift into a mutation tool (confirm/cancel/reschedule);
        # Phase 3 still renders the natural-language reply. Mutation
        # phrasings ("перенеси мою запись") are excluded inside
        # is_personal_booking_lookup and keep the LLM tool-choice path.
        elif is_personal_booking_lookup(text):
            logger.info(
                "booking.lookup.fastpath tool=%s mutation=false",
                SHOW_MY_BOOKINGS_TOOL_SPEC["name"],
            )
            first = CompletionResult(
                text="",
                tool_calls=[
                    ToolCall(
                        id="synth:my_bookings",
                        name=SHOW_MY_BOOKINGS_TOOL_SPEC["name"],
                        arguments={},
                    )
                ],
                provider="synth",
                finish_reason="tool_calls",
            )
        else:
            query_text = context.message_text

            # E0#1 Variant A (CR #955 F11 — moved past callback
            # short-circuits): pre-load the tenant master roster only
            # on the path that actually consumes it. The callback
            # branches (pick_master / pick_date) return early без
            # building a prompt, so loading there was wasted I/O.
            known_masters, known_masters_truncated = _load_tenant_master_roster(tenant)

            # D-10 — flow continuation. While skill_state["booking_flow"]
            # is fresh, exact cancel-vocab aborts the flow
            # deterministically (no LLM, no mutation); every other turn
            # gets the stored bookings + current local time injected into
            # the Phase-1 prompt so the single tool call can resolve
            # selection + datetime (staging D-10: ungrounded calls
            # degenerated to free-text «Подтверждаете?»).
            flow_state = _read_flow_state(context.conversation)
            if flow_state is not None and is_cancel_text(query_text):
                _clear_flow_state(context.conversation)
                logger.info(
                    "booking.flow.aborted tenant_id=%s conversation_id=%s "
                    "flow=%s stage=%s trace_id=%s",
                    tenant_id,
                    context.conversation.id,
                    flow_state.get("flow"),
                    flow_state.get("stage"),
                    context.trace_id,
                )
                return _build_skill_result(
                    text=_FLOW_ABORT_REPLIES.get(
                        str(flow_state.get("flow")),
                        _FLOW_ABORT_REPLIES["reschedule"],
                    ),
                    tool_calls_made=[],
                    confidence=_CONFIDENCE_OK,
                )
            flow_context = _flow_context_payload(flow_state, query_text=query_text)
            if flow_context is not None:
                logger.info(
                    "booking.flow.continuation tenant_id=%s conversation_id=%s "
                    "flow=%s stage=%s trace_id=%s",
                    tenant_id,
                    context.conversation.id,
                    flow_context.get("flow"),
                    flow_state.get("stage") if flow_state else "",
                    context.trace_id,
                )

            # ── Phase 1: first LLM call (decide on tool use) ───────
            first_messages = build_booking_prompt(
                brand_voice=_DEFAULT_BRAND_VOICE,
                query=query_text,
                known_masters=known_masters,
                known_masters_truncated=known_masters_truncated,
                flow_context=flow_context,
            )
            # #473 LLM Y3 envelope expansion: catch any LLMError (covers
            # UnknownTenantError from cost_tracker, LLMProviderUnavailable,
            # LLMTransportError, LLMQuotaError, LLMProviderQuotaExceeded)
            # and convert to friendly handoff. Pre-#473 these would
            # propagate as raw exceptions → 500 the customer.
            try:
                first = asyncio.run(
                    provider.complete(
                        first_messages,
                        model=model,
                        tools=get_active_booking_tool_specs(),
                    )
                )
            except LLMError as exc:
                logger.warning(
                    "booking.llm.first_complete_failed tenant=%s err_type=%s err=%s",
                    tenant_id,
                    type(exc).__name__,
                    exc,
                )
                return _handoff(
                    tool_calls_made=[],
                    reason="llm_error",
                    text=_FALLBACK_HANDOFF_TEXT,
                    tenant_id=tenant_id,
                )

        if not first.tool_calls:
            # Small talk / direct reply — no tool needed.
            return _build_skill_result(
                text=first.text,
                tool_calls_made=[],
                confidence=None,
            )

        first_call = first.tool_calls[0]
        tool_name = first_call.name
        tool_calls_made = first.tool_calls

        if tool_name not in {spec["name"] for spec in BOOKING_TOOL_SPECS}:
            # DRF-1005: was a silent handoff — log which tool the model
            # hallucinated so the reason is diagnosable from logs alone.
            logger.info("booking.unknown_tool tenant=%s tool=%s", tenant_id, tool_name)
            return _handoff(
                tool_calls_made=tool_calls_made,
                reason="booking_unknown_tool",
                text=_FALLBACK_HANDOFF_TEXT,
                tenant_id=tenant_id,
            )

        # ── Phase 2: tool dispatch ─────────────────────────────────
        tool_result, handoff_reason = _execute_tool(
            tool_name=tool_name,
            arguments=first_call.arguments,
            tenant=tenant,
            bot_user=context.bot_user,
            yclients=yclients,
            allowed_service_ids=allowed_service_ids,
            service_lookup=service_lookup,
            tenant_id=tenant_id,
        )

        # DRF-997: transient schedule-service outage is surfaced as a
        # deterministic retry message. Do NOT hand off to a manager.
        if tool_result.error == "schedule_unavailable":
            return _build_skill_result(
                text=tool_result.text,
                tool_calls_made=tool_calls_made,
                confidence=_CONFIDENCE_OK,
            )

        if handoff_reason:
            # DRF-1005: was a silent handoff — log the tool-error verdict.
            logger.info(
                "booking.tool_handoff tenant=%s tool=%s reason=%s",
                tenant_id,
                tool_name,
                handoff_reason,
            )
            return _handoff(
                tool_calls_made=tool_calls_made,
                reason=handoff_reason,
                text=_FALLBACK_HANDOFF_TEXT,
                tenant_id=tenant_id,
            )

        # Master-cards short-circuit: when show_masters returned candidates,
        # render them as a deterministic text + inline-keyboard (one button
        # per master, cb:book:pick_master:<id>:<service_id> payload). Skip
        # Phase 3 LLM to avoid the round-trip + remove the failure mode where
        # the model writes filler text instead of presenting the cards.
        if tool_name == SHOW_MASTERS_TOOL_SPEC["name"] and tool_result.masters:
            _audit_handled(tenant_id=tenant_id, tool=tool_name)
            service_id = _coerce_id(first_call.arguments.get("service_id"))
            if service_id is None:
                service_id = _resolve_service_id_by_name(
                    str(first_call.arguments.get("service_name") or ""), service_lookup
                )
            if service_id is None:
                # DRF-1005: was a silent handoff.
                logger.info("booking.show_masters.missing_service_context tenant=%s", tenant_id)
                return _handoff(
                    tool_calls_made=tool_calls_made,
                    reason="booking_missing_service_context",
                    text=_FALLBACK_HANDOFF_TEXT,
                    tenant_id=tenant_id,
                )
            return _build_skill_result(
                text=_MASTER_PICK_PROMPT,
                tool_calls_made=tool_calls_made,
                confidence=_CONFIDENCE_OK,
                action_data=_action_data_for_master_pick(
                    tool_result.masters, service_id=service_id
                ),
            )

        # Slot-cards short-circuit. When show_slots returned candidate
        # times, render them as a deterministic text + keyboard. Each
        # callback carries master_id + service_id + slot so the tap can
        # ground confirm_booking without relying on short-term memory.
        if tool_name == SHOW_SLOTS_TOOL_SPEC["name"] and tool_result.slots:
            _audit_handled(tenant_id=tenant_id, tool=tool_name)
            master_id = _coerce_id(first_call.arguments.get("master_id"))
            service_id = _coerce_id(first_call.arguments.get("service_id"))
            if master_id is None or service_id is None:
                # DRF-1005: was a silent handoff.
                logger.info("booking.show_slots.missing_context tenant=%s", tenant_id)
                return _handoff(
                    tool_calls_made=tool_calls_made,
                    reason="booking_missing_service_context",
                    text=_FALLBACK_HANDOFF_TEXT,
                    tenant_id=tenant_id,
                )
            # DRF-1325 — the part chip narrows the list to the bucket the
            # user asked for. An empty bucket is NOT rendered as an empty
            # keyboard: fall back to the whole day and say so, because a
            # message with no buttons is a dead end and the day's other
            # times are a real answer.
            slots = tool_result.slots
            narrowed = _slots_in_part(slots, part_filter) if part_filter else slots
            if part_filter and not narrowed:
                return _build_skill_result(
                    text=_PART_EMPTY_PROMPT.format(part=PART_CHIP_LABELS[part_filter].lower()),
                    tool_calls_made=tool_calls_made,
                    confidence=_CONFIDENCE_OK,
                    action_data=_action_data_for_slot_pick(
                        slots,
                        master_id=master_id,
                        service_id=service_id,
                    ),
                )
            return _build_skill_result(
                text=_SLOT_PICK_PROMPT,
                tool_calls_made=tool_calls_made,
                confidence=_CONFIDENCE_OK,
                action_data=_action_data_for_slot_pick(
                    narrowed,
                    master_id=master_id,
                    service_id=service_id,
                ),
            )

        # Health-check gate — only relevant for confirm_booking.
        if tool_name == CONFIRM_BOOKING_TOOL_SPEC["name"]:
            service_id = _coerce_id(first_call.arguments.get("service_id"))
            # DRF-1353: the resolved verdict is per (master × service), so the
            # master the LLM grounded must travel with the service id.
            gate_master_id = _coerce_id(first_call.arguments.get("master_id"))
            if service_id is not None and _service_requires_health_check(
                tenant, service_id, gate_master_id
            ):
                # DRF-1005: this branch used to hand off without a single
                # log line — log the policy decision, and use the policy
                # text (consultation), not the failure fallback.
                logger.info(
                    "booking.confirm.health_check_required tenant=%s service=%s",
                    tenant_id,
                    service_id,
                )
                return _handoff(
                    tool_calls_made=tool_calls_made,
                    reason="booking_health_check_required",
                    text=_HEALTH_CHECK_HANDOFF_TEXT,
                    tenant_id=tenant_id,
                )

        # ── Phase 3: second LLM call (natural-language reply) ──────
        # CR #955 F5 — DO NOT inject `known_masters` on Phase 3. The
        # `candidate_masters` block (from show_masters tool result) is
        # authoritative for the current service/time context; the full
        # roster would compete with candidates if a known-but-not-
        # offered master is mentioned by the customer, producing
        # contradictory advice. Phase 1's roster injection already did
        # its job — name-grounding for tool-use decision.
        second_messages = build_booking_prompt(
            brand_voice=_DEFAULT_BRAND_VOICE,
            query=context.message_text,
            known_masters=None,
            candidate_masters=_masters_payload(tool_result),
            available_slots=_slots_payload(tool_result),
            confirmation=_confirmation_payload(tool_result),
            pending=_pending_payload(tool_result),
            user_bookings=_bookings_payload(tool_result, tool_name),
            price=_price_payload(tool_result),
            certificate=_certificate_payload(tool_result),
        )
        # #473 LLM Y3 envelope expansion: same rationale as Phase 1
        # call site — catch all LLMError variants and produce a
        # friendly handoff instead of 500-ing the customer.
        try:
            second = asyncio.run(provider.complete(second_messages, model=model))
        except LLMError as exc:
            logger.warning(
                "booking.llm.second_complete_failed tenant=%s err_type=%s err=%s",
                tenant_id,
                type(exc).__name__,
                exc,
            )
            return _handoff(
                tool_calls_made=tool_calls_made,
                reason="llm_error",
                text=tool_result.text or _FALLBACK_HANDOFF_TEXT,
                tenant_id=tenant_id,
            )

        # Fall back to deterministic text when the model returns empty.
        reply_text = second.text or tool_result.text or _FALLBACK_HANDOFF_TEXT

        # D-10 — flow-state bookkeeping. A mutation-request turn that
        # ended with a bookings listing (disambiguation question) opens
        # the continuation state; a created preview (pending row) closes
        # it — the gate skill owns confirmation from here.
        if tool_name == SHOW_MY_BOOKINGS_TOOL_SPEC["name"]:
            _maybe_write_flow_state(
                context,
                message_text=context.message_text,
                tool_result=tool_result,
                tenant_id=tenant_id,
            )
        elif tool_result.pending is not None:
            _clear_flow_state(context.conversation)

        _audit_handled(tenant_id=tenant_id, tool=tool_name)
        action_data = _action_data_for_pending(tool_result)
        return _build_skill_result(
            text=reply_text,
            tool_calls_made=tool_calls_made,
            confidence=_CONFIDENCE_OK,
            action_data=action_data,
        )


# ---------------------------------------------------------------------------
# Tool dispatch helper
# ---------------------------------------------------------------------------


def _execute_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    tenant: Any,
    bot_user: Any,
    yclients: Any,
    allowed_service_ids: AbstractSet[int | str],
    service_lookup: dict[int | str, str],
    tenant_id: str,
) -> tuple[BookingToolResult, str]:
    """Run the chosen tool. Returns ``(result, handoff_reason_or_empty)``.

    Catches :class:`YClientsScheduleUnavailableError` raised while building the
    anti-hallucination allow-sets (e.g. a 429 on ``get_staff``) and surfaces it
    as the deterministic retry message instead of letting it propagate.
    """
    try:
        return _dispatch_tool(
            tool_name=tool_name,
            arguments=arguments,
            tenant=tenant,
            bot_user=bot_user,
            yclients=yclients,
            allowed_service_ids=allowed_service_ids,
            service_lookup=service_lookup,
            tenant_id=tenant_id,
        )
    except YClientsScheduleUnavailableError as exc:
        logger.warning("booking.tool.schedule_unavailable tool=%s err=%s", tool_name, exc)
        return BookingToolResult(text=SCHEDULE_UNAVAILABLE_TEXT, error="schedule_unavailable"), ""


def _dispatch_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    tenant: Any,
    bot_user: Any,
    yclients: Any,
    allowed_service_ids: AbstractSet[int | str],
    service_lookup: dict[int | str, str],
    tenant_id: str,
) -> tuple[BookingToolResult, str]:
    """Tool dispatch implementation (wrapped by :func:`_execute_tool`)."""
    if tool_name == SHOW_MASTERS_TOOL_SPEC["name"]:
        result = show_masters(client=yclients, arguments=arguments, tenant_id=tenant_id)
        # DRF-997: transient schedule outage (e.g. 429) is returned to the
        # user as a retry message, not a manager handoff.
        if result.error == "schedule_unavailable":
            return result, ""
        if result.error:
            return result, "booking_yclients_failure"
        if not result.masters:
            return result, "booking_no_masters"
        return result, ""

    if tool_name == SHOW_SLOTS_TOOL_SPEC["name"]:
        # The LLM may invoke show_slots without a prior show_masters in
        # the same turn (the orchestrator does NOT carry tool state
        # cross-turn yet). Fall back to the full staff list as the
        # allow-set so legitimate flows aren't blocked while still
        # rejecting outright fabrications.
        allow_masters = _fetch_master_ids(yclients)
        result = show_slots(
            client=yclients,
            arguments=arguments,
            tenant_id=tenant_id,
            allowed_master_ids=allow_masters,
        )
        if result.error == "invalid_master_id":
            return result, "booking_invalid_master_id"
        # DRF-997: transient schedule outage (e.g. 429) is returned to the
        # user as a retry message, not a manager handoff.
        if result.error == "schedule_unavailable":
            return result, ""
        if result.error:
            return result, "booking_yclients_failure"
        return result, ""

    if tool_name == CONFIRM_BOOKING_TOOL_SPEC["name"]:
        allow_masters = _fetch_master_ids(yclients)
        master_lookup = _fetch_master_lookup(yclients)
        result = confirm_booking(
            client=yclients,
            arguments=arguments,
            tenant=tenant,
            bot_user=bot_user,
            allowed_master_ids=allow_masters,
            allowed_service_ids=allowed_service_ids,
            master_lookup=master_lookup,
            service_lookup=service_lookup,
        )
        if result.error == "invalid_master_id":
            return result, "booking_invalid_master_id"
        if result.error == "invalid_service_id":
            return result, "booking_invalid_service_id"
        # DRF-997: transient schedule outage (e.g. 429) is returned to the
        # user as a retry message, not a manager handoff.
        if result.error == "schedule_unavailable":
            return result, ""
        if result.error in {"yclients_unavailable", "yclients_api_error"}:
            return result, "booking_yclients_failure"
        if result.error:
            return result, "booking_yclients_failure"
        return result, ""

    if tool_name == CANCEL_BOOKING_TOOL_SPEC["name"]:
        result = cancel_booking(
            client=yclients,
            arguments=arguments,
            tenant=tenant,
            bot_user=bot_user,
        )
        if result.error == "invalid_record_id":
            return result, "booking_invalid_record_id"
        # DRF-997: transient schedule outage (e.g. 429) is returned to the
        # user as a retry message, not a manager handoff.
        if result.error == "schedule_unavailable":
            return result, ""
        if result.error:
            return result, "booking_yclients_failure"
        return result, ""

    if tool_name == RESCHEDULE_BOOKING_TOOL_SPEC["name"]:
        result = reschedule_booking(
            client=yclients,
            arguments=arguments,
            tenant=tenant,
            bot_user=bot_user,
        )
        if result.error == "invalid_record_id":
            return result, "booking_invalid_record_id"
        # DRF-997: transient schedule outage (e.g. 429) is returned to the
        # user as a retry message, not a manager handoff.
        if result.error == "schedule_unavailable":
            return result, ""
        if result.error == "slot_unavailable":
            # NOT a handoff — the LLM should phrase the clarification
            # itself ("на это время уже занято — могу подобрать
            # соседний слот?"). Return ``result`` with no handoff
            # reason; the skill's second LLM call will see the empty
            # ``pending`` field and pick the clarification template.
            return result, ""
        if result.error == "invalid_datetime":
            return result, ""
        if result.error == "past_datetime":
            return result, ""
        if result.error == "record_not_found":
            return result, "booking_invalid_record_id"
        if result.error:
            return result, "booking_yclients_failure"
        return result, ""

    if tool_name == SHOW_MY_BOOKINGS_TOOL_SPEC["name"]:
        result = show_my_bookings(client=yclients, tenant=tenant, bot_user=bot_user)
        # No handoff path for read-only listing — empty is OK.
        return result, ""

    if tool_name == CALC_PRICE_TOOL_SPEC["name"]:
        result = calc_price(
            tenant=tenant,
            arguments=arguments,
            allowed_service_ids=allowed_service_ids,
            service_lookup=service_lookup,
            # DRF-1067: the provider resolves the master+service edge price
            # when the LLM passes master_id (Ayla path only).
            client=yclients,
        )
        if result.error == "price_invalid_service_id":
            return result, "price_invalid_service_id"
        # promo_status values (not_found / expired / wrong_service / ...)
        # are part of the answer, NOT handoff triggers — the user just
        # gets a polite "не нашла такой промокод" reply.
        return result, ""

    if tool_name == BUY_CERTIFICATE_TOOL_SPEC["name"]:
        result = buy_certificate(
            tenant=tenant,
            bot_user=bot_user,
            arguments=arguments,
        )
        # ``amount_out_of_range`` and ``certificate_disabled`` (B2
        # feature-flag short-circuit) are clarifications, NOT handoffs
        # — the LLM rephrases the polite "сумма должна быть от ... до"
        # / "функция готовится" response. Only the provider failure
        # path triggers an operator handoff.
        if result.error == "certificate_provider_failure":
            return result, "certificate_provider_failure"
        return result, ""

    return BookingToolResult(error="unknown_tool"), "booking_unknown_tool"


def _fetch_master_ids(yclients: Any) -> set[int | str]:
    try:
        return {_id_key(s.id) for s in yclients.get_staff(staff_id=None)}
    except YClientsScheduleUnavailableError:
        # DRF-997: do not silently disable the anti-hallucination guard on a
        # transient 429. Let the caller surface the retry text.
        raise
    except Exception:  # noqa: BLE001 — defensive; caller handles handoff
        return set()


def _fetch_master_lookup(yclients: Any) -> dict[int | str, str]:
    try:
        return build_master_lookup(yclients.get_staff(staff_id=None))
    except YClientsScheduleUnavailableError:
        # DRF-997: same guard as _fetch_master_ids.
        raise
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Health-check gate
# ---------------------------------------------------------------------------


def _health_check_gate_disabled_for_tenant() -> bool:
    """DRF-1005: True when the ACTIVE tenant is in the pilot allowlist.

    The tenant identity comes from the active ``tenant_scope`` (same
    lazy-import pattern as ``apps/integrations/ayla/booking_client.py``,
    DRF-997/1004) — never from caller-supplied data.

    Fail-closed on every doubt: no tenant in scope, or a malformed
    setting value injected past settings load (``override_settings`` /
    live reload), keeps the gate CLOSED. A malformed value can never
    silently widen access, and a settings-load-time malformed value never
    boots at all (``config/settings/base.py`` raises
    ``ImproperlyConfigured``).
    """
    from django.conf import settings

    from apps.eventbus.ingest_allowlist import (
        AllowlistConfigurationError,
        parse_tenant_allowlist,
    )
    from apps.tenancy.context import current_tenant

    tenant = current_tenant()
    if tenant is None:
        return False
    raw: Any = getattr(settings, "BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS", frozenset())
    try:
        allowed = parse_tenant_allowlist(
            raw, setting_name="BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS"
        )
    except AllowlistConfigurationError as exc:
        logger.warning("booking.health_gate.allowlist_malformed err=%s", exc)
        return False
    return str(tenant.id).lower() in allowed


def _resolved_health_check_for_edge(
    tenant: Any,
    master_id: int | str | None,
    service_id: int | str,
) -> bool | None:
    """Ayla's RESOLVED (master×service) screening verdict, or ``None``.

    DRF-1353. Reads ``MasterService.resolved_requires_health_check`` — the
    mirror of Ayla's ``SpecialistService.resolved_requires_health_check``,
    an escalate-only OR across the canonical template floor, the salon
    service, and the specialist override. That OR is why the service-level
    ``CatalogService.requires_health_check`` alone is NOT a safe read: it
    carries exactly one of the three inputs, and 102 of Ayla's 1223
    canonical service templates set the floor.

    ``None`` means UNKNOWN and is returned for every path where we cannot
    prove a verdict: no master in hand, an id that is not a UUID, no edge
    row (operator-owned MM4 pair, or catalog sync has not reached this
    tenant), or a row whose column was never synced. The caller keeps the
    gate CLOSED on ``None`` — absence of evidence is not evidence of
    safety for a medical check.
    """
    if master_id is None:
        return None
    try:
        from apps.catalog.models import MasterService
    except ImportError:  # pragma: no cover — catalog always available
        return None
    try:
        master_key = uuid.UUID(str(master_id))
        service_key = uuid.UUID(str(service_id))
    except (ValueError, AttributeError, TypeError):
        # A stray legacy int (or anything else that is not an Ayla id) cannot
        # name an edge. Unresolvable, therefore unknown — never permissive.
        logger.info(
            "booking.health_gate.unresolvable_edge master=%s service=%s",
            master_id,
            service_id,
        )
        return None
    return (
        MasterService.all_tenants.filter(
            tenant=tenant,
            master_id=master_key,
            service__ayla_service_id=service_key,
        )
        .values_list("resolved_requires_health_check", flat=True)
        .first()
    )


def _service_requires_health_check(
    tenant: Any,
    service_id: int | str,
    master_id: int | str | None = None,
) -> bool:
    """Whether booking ``service_id`` must route through a human health check.

    **flag-OFF (legacy YClients path):** the catalog mirror is the source
    of truth, keyed by the int ``external_id``. If we can't find the
    service row (e.g. catalog isn't synced yet) we DEFAULT to ``False`` —
    better UX to attempt the booking than to dead-end every flow.

    **flag-ON (Ayla REST path), DRF-1353.** The resolved (master×service)
    source that #1034/#1121 called missing now exists and is mirrored:
    ``MasterService.resolved_requires_health_check``. Precedence:

    1. **The resolved verdict wins, in both directions.** ``True`` gates,
       ``False`` opens. It wins over the DRF-1005 allowlist too — an
       allowlisted tenant must not be able to book a service Ayla says
       needs screening. That ordering is a tightening, not a loosening:
       before DRF-1353 the single allowlisted pilot tenant was the one
       tenant for which the gate could never fire at all.
    2. **Unknown (``None``) falls back to the DRF-1005 allowlist**, which
       keeps its original job: unblock a pilot tenant whose edges are not
       mirrored (operator-owned MM4 rows, sync not yet run).
    3. **Otherwise fail closed** — unchanged from #1034.

    Note what this gate is and is not. No other booking entry point in this
    codebase consults it — ``apps/booking/services/create.py``,
    ``apps/admin_api/views_booking_create.py`` and the miniapp all create
    bookings without reading the flag — and Ayla's ``appointments`` app does
    not enforce it server-side either. It is the conversational channel's
    routing policy ("hand this one to a human"), not a system-wide safety
    interlock.
    """
    if _booking_via_ayla():
        resolved = _resolved_health_check_for_edge(tenant, master_id, service_id)
        if resolved is not None:
            logger.info(
                "booking.health_gate.resolved tenant=%s master=%s service=%s gated=%s",
                getattr(tenant, "id", "?"),
                master_id,
                service_id,
                resolved,
            )
            return bool(resolved)
        if _health_check_gate_disabled_for_tenant():
            # DRF-1005: owner-mandated audit trail — disabling a medical
            # screening check must be traceable, never invisible.
            logger.info(
                "booking.health_check_gate.disabled tenant=%s service=%s",
                getattr(tenant, "id", "?"),
                service_id,
            )
            write_audit(
                EVENT_BOOKING_HEALTH_GATE_DISABLED,
                target="BookingSkill",
                payload={
                    "tenant_id": str(getattr(tenant, "id", "")),
                    "service_id": str(service_id),
                    "master_id": str(master_id or ""),
                    "reason": "resolved_flag_unknown",
                },
            )
            return False
        # Edge not mirrored and tenant not allowlisted → fail closed. See #1034.
        return True

    try:
        from apps.catalog.models import CatalogService
    except ImportError:  # pragma: no cover — catalog always available
        # No catalog to ground the legacy YClients path against → lenient default.
        return False

    row = (
        CatalogService.all_tenants.filter(
            tenant=tenant,
            external_id=int(service_id),
        )
        .only("requires_health_check")
        .first()
    )
    if row is None:
        return False
    return bool(row.requires_health_check)


# ---------------------------------------------------------------------------
# Payload helpers for second LLM call
# ---------------------------------------------------------------------------


def _masters_payload(result: BookingToolResult) -> list[dict[str, Any]] | None:
    if not result.masters:
        return None
    return [
        {
            "id": m.id,
            "name": m.name,
            "specialization": m.specialization,
        }
        for m in result.masters
    ]


def _action_data_for_master_pick(
    masters: list,
    service_id: int | str | None = None,
) -> dict[str, Any]:
    """Build the master-cards keyboard from a show_masters result.

    Channel adapters consume the platform-canonical envelope shape (same
    as preview-gated confirms in :func:`_action_data_for_pending`). The
    handler ``_build_attachments`` converts the channel-agnostic
    ``[{label, callback}]`` list to the MAX wire format.

    One button per master. The callback carries both the selected master
    id and the service id so the downstream date/slot lookups are self-
    contained: ``cb:book:pick_master:<staff_id>:<service_id>``. The skill's
    ``matches()`` picks the prefix up on the tap and ``handle()`` dispatches
    the date picker without a Phase 1 LLM round-trip.

    Label shape: ``<emoji> <name>`` with a specialisation hint when
    distinct from the canonical "Мастер массажа" boilerplate — keeps the
    button readable in MAX's tight inline-keyboard layout.
    """
    service_suffix = f":{service_id}" if service_id is not None else ""
    buttons = [
        {
            "label": _master_button_label(m),
            "callback": f"{CALLBACK_BOOK_PICK_MASTER_PREFIX}{m.id}{service_suffix}",
        }
        for m in masters
    ]
    return {
        "attachments": [
            {
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            }
        ],
        "kind": "master_pick",
    }


def _master_button_label(master) -> str:
    """One-line readable label for the master-pick button."""
    name = (master.name or "").strip() or "Мастер"
    spec = (master.specialization or "").strip()
    if spec and spec.lower() != "мастер массажа":
        return f"👤 {name} — {spec}"
    return f"👤 {name}"


def _resolve_service_id_by_name(
    service_name: str,
    service_lookup: dict[int | str, str],
) -> int | str | None:
    """Map a free-text service name to a catalog id.

    Resolution is fuzzy but conservative: a match is accepted only when the
    normalized user input is an exact, substring, or superstring match of
    exactly one catalog title. Multiple matches mean the request is ambiguous
    and the caller must hand off rather than guess.

    Returns ``None`` when the name is empty, not found, or ambiguous, so the
    caller routes to ``booking_missing_service_context`` instead of emitting a
    broken master-pick keyboard.
    """
    needle = _normalize_service_text(service_name)
    if not needle:
        return None
    matches: list[int | str] = []
    for service_id, title in service_lookup.items():
        norm_title = _normalize_service_text(title)
        if norm_title == needle or needle in norm_title or norm_title in needle:
            matches.append(service_id)
    return matches[0] if len(matches) == 1 else None


def _normalize_service_text(value: str) -> str:
    """Lower-case, collapse whitespace and strip punctuation for matching."""
    lowered = value.lower().strip()
    # Drop common punctuation so "маникюр классический" and "маникюр, классический"
    # still match. Keep letters, digits and spaces.
    cleaned = "".join(ch for ch in lowered if ch.isalnum() or ch.isspace())
    return " ".join(cleaned.split())


def _is_iso_datetime(value: str) -> bool:
    """Return True when ``value`` parses as an ISO-8601 datetime.

    Callback payloads that carry ``<master>:<service>:<datetime>`` must not
    have their datetime portion mistaken for master/service ids when an old
    ``<datetime>``-only payload is received after the format change.
    """
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _action_data_for_slot_pick(
    slots: list,
    master_id: int | str,
    service_id: int | str,
) -> dict[str, Any]:
    """Build the slot-cards keyboard from a show_slots result.

    One button per slot. Callback carries master, service and slot so
    the tap can ground confirm_booking deterministically:
    ``cb:book:pick_slot:<master_id>:<service_id>:<iso_datetime>``.
    """
    buttons = [
        {
            "label": _slot_button_label(s),
            "callback": f"{CALLBACK_BOOK_PICK_SLOT_PREFIX}{master_id}:{service_id}:{s.datetime}",
        }
        for s in slots
    ]
    return {
        "attachments": [
            {
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            }
        ],
        "kind": "slot_pick",
    }


# Russian weekday abbreviations for slot button labels.
_RU_WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_RU_MONTHS_SHORT = (
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)


def _handle_pick_slot_callback(
    *,
    text: str,
    context: SkillContext,
    tenant: Any,
    tenant_id: str,
    yclients: Any,
    allowed_service_ids: AbstractSet[int | str],
    service_lookup: dict[int | str, str],
) -> SkillResult:
    """Deterministic ``cb:book:pick_slot:`` handling (RB1.1-D05).

    Same architectural pattern as the pick_master / pick_date
    short-circuits: the callback payload is self-contained, so the whole
    path runs without a Phase-1 LLM tool-choice call. Steps:

    1. Parse + schema-validate the payload (UUID/int ids, ISO datetime).
    2. Validate the service against the prefetched tenant catalog and
       the specialist against the live tenant roster — both lookups are
       tenant-scoped, so this is also the tenant-ownership check.
    3. Health-check gate — identical to the LLM confirm path.
    4. Duplicate-tap reuse: an identical active CONFIRM pending returns
       the same preview token instead of stacking a second row.
    5. Slot availability re-check against the provider (the keyboard may
       be minutes old); a taken slot renders fresh alternatives, never a
       pending action.
    6. ``confirm_booking`` builds the preview + persists the
       :class:`PendingBookingAction` — the exact same validator/writer
       the LLM path uses. The ✅ tap then executes through
       :mod:`apps.bookings.callbacks` as usual.

    A refused tap is recovered locally (no handoff, no backend call, no
    pending row) — the user is asked to restart the selection. Which
    refusal it was is now said out loud, in the reply and in the journal
    both: see :func:`_refuse_callback` and the ``REFUSAL_*`` vocabulary.
    Only step 5's past-slot branch is allowed to call it «устарел».
    """
    raw_payload = text[len(CALLBACK_BOOK_PICK_SLOT_PREFIX) :].strip()
    parts = raw_payload.split(":", 2)
    if len(parts) != 3:
        logger.warning("booking.pick_slot.bad_payload raw=%r", raw_payload)
        return _build_skill_result(
            text="Не удалось распознать время. Напишите ещё раз?",
            tool_calls_made=[],
            confidence=None,
        )
    raw_master, raw_service, raw_dt = parts
    master_id = _coerce_id(raw_master)
    service_id = _coerce_id(raw_service)
    if master_id is None or service_id is None or not raw_dt or not _is_iso_datetime(raw_dt):
        # Nothing expired here — the button is simply unreadable. Naming
        # which of the three fields failed is the whole point (DRF-1473).
        bad = [
            name
            for name, ok in (
                ("master_id", master_id is not None),
                ("service_id", service_id is not None),
                ("slot_datetime", bool(raw_dt) and _is_iso_datetime(raw_dt)),
            )
            if not ok
        ]
        return _refuse_callback(
            step="pick_slot",
            reason=REFUSAL_MALFORMED_CALLBACK,
            text=_BROKEN_CALLBACK_TEXT,
            detail=f"fields={','.join(bad)} raw={raw_payload!r}",
        )

    # Service must exist in the tenant catalog (prefetched in handle()).
    if service_id not in allowed_service_ids:
        return _refuse_callback(
            step="pick_slot",
            reason=REFUSAL_UNKNOWN_SERVICE,
            text=_CONTEXT_GONE_TEXT,
            detail=f"service={service_id} catalog_size={len(allowed_service_ids)}",
        )
    # Specialist must be on the live tenant roster. One staff fetch feeds
    # both the membership check and the display-name lookup; a provider
    # failure here is a handoff, NOT a "stale context" verdict — the two
    # failure modes must stay distinguishable for the user.
    try:
        staff_rows = yclients.get_staff(staff_id=None)
    except (YClientsAPIError, YClientsUnavailableError) as exc:
        logger.warning("booking.pick_slot.staff_failed err=%s", exc)
        return _handoff(
            tool_calls_made=[],
            reason="booking_yclients_failure",
            text=_FALLBACK_HANDOFF_TEXT,
            tenant_id=tenant_id,
        )
    allowed_master_ids = {_id_key(s.id) for s in staff_rows}
    master_lookup = build_master_lookup(staff_rows)
    if master_id not in allowed_master_ids:
        return _refuse_callback(
            step="pick_slot",
            reason=REFUSAL_UNKNOWN_MASTER,
            text=_CONTEXT_GONE_TEXT,
            detail=f"master={master_id} roster_size={len(allowed_master_ids)}",
        )

    # Health-check gate — same rule as the LLM confirm path: gated
    # services route to a human instead of rendering a preview.
    if _service_requires_health_check(tenant, service_id, master_id):
        # DRF-1005: log the policy decision (this branch used to hand off
        # silently) and use the policy text, not the failure fallback.
        logger.info(
            "booking.pick_slot.health_check_required tenant=%s master=%s service=%s",
            tenant_id,
            master_id,
            service_id,
        )
        return _handoff(
            tool_calls_made=[],
            reason="booking_health_check_required",
            text=_HEALTH_CHECK_HANDOFF_TEXT,
            tenant_id=tenant_id,
        )

    # A past slot means the keyboard outlived itself — recover locally
    # instead of hitting the provider with a date it will 4xx on.
    tapped_dt = datetime.fromisoformat(raw_dt)  # validated above
    now = timezone.now()
    if tapped_dt.tzinfo is None:
        now = now.replace(tzinfo=None)
    if tapped_dt < now:
        return _refuse_callback(
            step="pick_slot",
            reason=REFUSAL_EXPIRED_SLOT,
            text=_STALE_CONTEXT_TEXT,
            detail=f"slot={raw_dt} now={now.isoformat()}",
        )

    # Duplicate tap on the same slot button: reuse the identical active
    # preview instead of stacking a second PendingBookingAction row.
    existing = _find_identical_active_confirm_pending(
        tenant=tenant,
        bot_user=context.bot_user,
        master_id=master_id,
        service_id=service_id,
        slot_datetime=raw_dt,
    )
    if existing is not None:
        return _skill_result_for_existing_pending(
            existing,
            context=context,
            tenant_id=tenant_id,
        )

    # Slot availability re-check — the slot keyboard may be stale by tap
    # time (D05-D). A taken slot never produces a pending action; the
    # user gets fresh alternatives for the same day instead.
    target_date = raw_dt[:10]
    try:
        times = yclients.get_available_times(
            staff_id=master_id,
            date=target_date,
            service_ids=[service_id],
        )
    except YClientsScheduleUnavailableError as exc:
        logger.warning("booking.pick_slot.schedule_unavailable master=%s err=%s", master_id, exc)
        return _build_skill_result(
            text=SCHEDULE_UNAVAILABLE_TEXT,
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
        )
    except (YClientsAPIError, YClientsUnavailableError) as exc:
        logger.warning("booking.pick_slot.slots_failed master=%s err=%s", master_id, exc)
        return _handoff(
            tool_calls_made=[],
            reason="booking_yclients_failure",
            text=_FALLBACK_HANDOFF_TEXT,
            tenant_id=tenant_id,
        )
    slots = [c for c in (_to_slot_candidate(t, target_date) for t in times) if c is not None]
    if not any(_same_slot_instant(c.datetime, raw_dt) for c in slots):
        logger.info("booking.pick_slot.slot_gone master=%s slot=%s", master_id, raw_dt)
        if slots:
            return _build_skill_result(
                text=_SLOT_TAKEN_PROMPT,
                tool_calls_made=[],
                confidence=_CONFIDENCE_OK,
                action_data=_action_data_for_slot_pick(
                    slots,
                    master_id=master_id,
                    service_id=service_id,
                ),
            )
        return _build_skill_result(
            text=_SLOT_TAKEN_NO_ALTERNATIVES,
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
        )

    # Deterministic preview — confirm_booking validates + persists the
    # PendingBookingAction exactly as on the LLM path. The synthesised
    # ToolCall is recorded in tool_calls_made for telemetry parity with
    # the pick_date short-circuit.
    synth_call = ToolCall(
        id=f"synth:pick_slot:{master_id}:{service_id}:{raw_dt}",
        name=CONFIRM_BOOKING_TOOL_SPEC["name"],
        arguments={
            "master_id": master_id,
            "service_id": service_id,
            "slot_datetime": raw_dt,
        },
    )
    result = confirm_booking(
        client=yclients,
        arguments=synth_call.arguments,
        tenant=tenant,
        bot_user=context.bot_user,
        allowed_master_ids=allowed_master_ids,
        allowed_service_ids=allowed_service_ids,
        master_lookup=master_lookup,
        service_lookup=service_lookup,
    )
    if result.error in {"invalid_master_id", "invalid_service_id", "missing_slot"}:
        # Pre-validated above; a roster/catalog race mid-flow lands here.
        # Recoverable locally — no handoff, no pending row.
        return _refuse_callback(
            step="pick_slot",
            reason=REFUSAL_VALIDATION_RACE,
            text=_CONTEXT_GONE_TEXT,
            detail=f"err={result.error}",
        )
    if result.error:
        # DRF-1005: was a silent handoff — log the confirm_booking error.
        logger.info("booking.pick_slot.confirm_failed tenant=%s err=%s", tenant_id, result.error)
        return _handoff(
            tool_calls_made=[synth_call],
            reason="booking_yclients_failure",
            text=_FALLBACK_HANDOFF_TEXT,
            tenant_id=tenant_id,
        )

    # At most one active create-preview per user is enforced inside
    # confirm_booking itself (it supersedes earlier unconsumed CONFIRM
    # pendings next to row creation), so this path needs no extra step.

    # A created preview closes any D-10 continuation state — the
    # confirmation gate owns the flow from here (mirrors the LLM path).
    _clear_flow_state(context.conversation)
    _audit_handled(tenant_id=tenant_id, tool=CONFIRM_BOOKING_TOOL_SPEC["name"])
    return _build_skill_result(
        text=result.text,
        tool_calls_made=[synth_call],
        confidence=_CONFIDENCE_OK,
        action_data=_action_data_for_pending(result),
    )


def _same_slot_instant(candidate: str, tapped: str) -> bool:
    """True when two ISO datetimes denote the same slot.

    Exact string equality first (the common case — the tapped value came
    from a slot card rendered off the same provider field); parsed
    comparison second to tolerate offset-format drift. A naive/aware mix
    compares unequal, so it counts as different unless the strings
    matched.
    """
    if candidate == tapped:
        return True
    try:
        cand_dt = datetime.fromisoformat(candidate)
        tapped_dt = datetime.fromisoformat(tapped)
    except (TypeError, ValueError):
        return False
    if (cand_dt.tzinfo is None) != (tapped_dt.tzinfo is None):
        # The provider emits both shapes (offset-aware ``datetime`` field,
        # naive ``time`` fallback). A naive/aware mix compares unequal
        # even for the same wall-clock slot — compare wall clocks so the
        # re-check doesn't report the very slot on the keyboard as taken.
        return cand_dt.replace(tzinfo=None) == tapped_dt.replace(tzinfo=None)
    return bool(cand_dt == tapped_dt)


def _find_identical_active_confirm_pending(
    *,
    tenant: Any,
    bot_user: Any,
    master_id: int | str,
    service_id: int | str,
    slot_datetime: str,
) -> PendingBookingAction | None:
    """Latest unconsumed, unexpired CONFIRM pending with the same payload.

    Duplicate-tap safety for the deterministic pick_slot path: a second
    tap on the same slot button returns the existing preview instead of
    stacking another row. Payload ids are compared as strings so the
    flag-OFF int path and the flag-ON UUID path behave identically.
    """
    rows = PendingBookingAction.all_tenants.filter(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.CONFIRM,
        consumed_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at")[:10]
    for row in rows:
        payload = row.payload or {}
        if (
            str(payload.get("master_id")) == str(master_id)
            and str(payload.get("service_id")) == str(service_id)
            and _same_slot_instant(str(payload.get("slot_datetime") or ""), slot_datetime)
        ):
            return row
    return None


def _skill_result_for_existing_pending(
    row: PendingBookingAction,
    *,
    context: SkillContext,
    tenant_id: str,
) -> SkillResult:
    """Rebuild the preview card for an already-active pending row.

    The snapshot stored on the row (names + slot) is authoritative — no
    provider round-trip needed to re-render the same card with the same
    token, so both taps converge on one PendingBookingAction. Flow state
    is cleared exactly as on the new-preview path: the confirmation gate
    owns the flow from here.
    """
    payload = row.payload or {}
    preview_text = _format_confirm_preview(
        master_name=str(payload.get("master_name") or ""),
        service_name=str(payload.get("service_name") or ""),
        slot_datetime=str(payload.get("slot_datetime") or ""),
    )
    result = BookingToolResult(
        text=preview_text,
        pending=PendingPreview(
            kind=PendingBookingAction.Kind.CONFIRM,
            token=row.pk,
            preview_text=preview_text,
            keyboard=confirm_2_button(str(row.pk)),
        ),
    )
    _clear_flow_state(context.conversation)
    _audit_handled(tenant_id=tenant_id, tool=CONFIRM_BOOKING_TOOL_SPEC["name"])
    return _build_skill_result(
        text=preview_text,
        tool_calls_made=[],
        confidence=_CONFIDENCE_OK,
        action_data=_action_data_for_pending(result),
    )


def _render_date_picker(
    *,
    master_id: int | str,
    service_id: int | str,
    yclients: Any,
    tenant_id: str,
    tenant: Any = None,
    pref: TimePreference | None = None,
    expand_all: bool = False,
) -> SkillResult:
    """Build the "when?" reply after a master pick.

    DRF-1325 changed WHAT is rendered, not where it comes from. Before,
    every one of the master's free days went out as a keyboard of
    ``2026-08-28``-shaped buttons — a bare calendar. Now the first three
    free days carry human captions («Сегодня» / «Завтра» / «Послезавтра»,
    a dated caption after that) and the rest hide behind «Выбрать дату»,
    which re-enters here with ``expand_all`` and renders the old full list.
    And when the user already SAID when («завтра вечером»), the day question
    is skipped entirely: the answer goes straight to that day — or, if the
    master has nothing on it, says so in one line and falls back to the
    chips rather than dropping the request in silence.

    Every caption is derived from ``dates``, the days the schedule read
    actually returned. That is not an availability guarantee (there is no
    authoritative availability contract; ``create`` still owns the final
    409 — ``docs/OD_SALON_P0_CONTRACT.md``). It is the weaker, honest
    property the ticket demands: a chip leads to something.

    Calls ``client.get_available_dates`` directly (no tool wrapper —
    the dates list isn't an LLM-grounded artefact, it's pure ops data).
    Renders the first :data:`_MAX_DATE_BUTTONS` dates as inline-keyboard
    buttons with ``cb:book:pick_date:<master_id>:<YYYY-MM-DD>:<service_id>``
    payloads.

    YClients failures fold into the same handoff path as the rest of
    the booking flow — UX is "переключаю на менеджера" rather than a
    raw error.
    """
    try:
        service_ids = [service_id] if service_id is not None else None
        dates = yclients.get_available_dates(staff_id=master_id, service_ids=service_ids)
    except YClientsScheduleUnavailableError as exc:
        # DRF-997: transient 429 / rate-limit on the date picker. Keep the
        # bot in the conversation with an honest retry message instead of
        # handing off to a manager.
        logger.warning("booking.pick_master.schedule_unavailable master=%s err=%s", master_id, exc)
        return _build_skill_result(
            text=SCHEDULE_UNAVAILABLE_TEXT,
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
        )
    except (YClientsAPIError, YClientsUnavailableError) as exc:
        logger.warning("booking.pick_master.yclients_failed master=%s err=%s", master_id, exc)
        return _handoff(
            tool_calls_made=[],
            reason="booking_yclients_failure",
            text=_FALLBACK_HANDOFF_TEXT,
            tenant_id=tenant_id,
        )

    if not dates:
        # Valid empty result — render a friendly "no dates" reply so the
        # user knows to pick a different master. Not a handoff — the
        # bot still owns the conversation, just routes back to master list.
        return _build_skill_result(
            text=_DATE_PICKER_FALLBACK_NO_DATES,
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
        )

    # «Сегодня» is computed in the SALON's zone, never the server's: the
    # server runs on UTC and Moscow is three hours ahead of it, so a
    # server-side "today" is the salon's yesterday for the first three hours
    # of every day. day_label falls back to a dated caption for anything
    # outside today..+2, so a stale day can never wear a relative word.
    today = local_today(tenant)
    ordered = sorted(dates)

    # The user already named a day. Honouring it is the whole ticket.
    wanted = resolve_date(pref, today)
    if wanted and not expand_all:
        if wanted in ordered:
            return _render_part_picker(
                master_id=master_id,
                service_id=service_id,
                date=wanted,
                yclients=yclients,
                tenant_id=tenant_id,
                tenant=tenant,
                pref=pref,
                heard=describe(pref, wanted, today),
            )
        # Asked for a day the master has nothing on. Saying so IS the
        # point: silence here is exactly the defect this ticket names.
        logger.info("booking.time_pref.day_unavailable master=%s date=%s", master_id, wanted)
        return _build_skill_result(
            text=_DAY_UNAVAILABLE_PROMPT.format(day=day_label(wanted, today).lower()),
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
            action_data=_action_data_for_date_pick(
                master_id,
                ordered[:_MAX_DATE_BUTTONS],
                service_id,
                today=today,
                collapse=True,
            ),
        )

    capped = ordered[:_MAX_DATE_BUTTONS]
    return _build_skill_result(
        text=_DATE_PICK_PROMPT,
        tool_calls_made=[],
        confidence=_CONFIDENCE_OK,
        action_data=_action_data_for_date_pick(
            master_id, capped, service_id, today=today, collapse=not expand_all
        ),
    )


def _render_part_picker(
    *,
    master_id: int | str,
    service_id: int | str,
    date: str,
    yclients: Any,
    tenant_id: str,
    tenant: Any = None,
    pref: TimePreference | None = None,
    heard: str = "",
) -> SkillResult:
    """Ask «утро / день / вечер» for one day — or skip straight to the times.

    The buckets come from :mod:`apps.orchestrator.time_preference`, the one
    place that defines where morning ends and evening begins, so the word on
    the chip means the same thing here, in the parse of «завтра вечером» and
    in the readback.

    Three shortcuts keep this from adding a pointless tap:

    * the user already named a part and it has slots → those slots;
    * exactly one part has slots → that part's slots (a one-button question
      is not a question);
    * the day has no slots at all → the honest line instead of an empty
      keyboard.
    """
    try:
        service_ids = [service_id] if service_id is not None else None
        times = yclients.get_available_times(staff_id=master_id, date=date, service_ids=service_ids)
    except YClientsScheduleUnavailableError as exc:
        logger.warning("booking.pick_date.schedule_unavailable master=%s err=%s", master_id, exc)
        return _build_skill_result(
            text=SCHEDULE_UNAVAILABLE_TEXT,
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
        )
    except (YClientsAPIError, YClientsUnavailableError) as exc:
        logger.warning("booking.pick_date.yclients_failed master=%s err=%s", master_id, exc)
        return _handoff(
            tool_calls_made=[],
            reason="booking_yclients_failure",
            text=_FALLBACK_HANDOFF_TEXT,
            tenant_id=tenant_id,
        )

    slots = [c for c in (_to_slot_candidate(t, date) for t in times) if c is not None]
    today = local_today(tenant)
    if not slots:
        return _build_skill_result(
            text=_DAY_UNAVAILABLE_PROMPT.format(day=day_label(date, today).lower()),
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
        )

    present = [p for p in PART_ORDER if _slots_in_part(slots, p)]

    wanted_part = pref.part if pref is not None else None
    if wanted_part is not None and wanted_part in present:
        # These two branches render a slot keyboard without going through the
        # show_slots TOOL, so the audit row the tool would have written has to
        # be written here — a rendered slot list must be visible in the audit
        # trail no matter which code path produced it.
        _audit_handled(tenant_id=tenant_id, tool=SHOW_SLOTS_TOOL_SPEC["name"])
        return _build_skill_result(
            text=_slot_prompt(date, wanted_part, today, heard=heard),
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
            action_data=_action_data_for_slot_pick(
                _slots_in_part(slots, wanted_part),
                master_id=master_id,
                service_id=service_id,
            ),
        )

    if wanted_part is not None and present:
        # Asked for an evening this day does not have. Name the gap, then
        # offer what the day really holds — never a silent substitution.
        logger.info(
            "booking.time_pref.part_unavailable master=%s date=%s part=%s",
            master_id,
            date,
            wanted_part,
        )
        return _build_skill_result(
            text=_PART_UNAVAILABLE_PROMPT.format(
                day=day_label(date, today),
                part=PART_PHRASES[wanted_part],
            ),
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
            action_data=_action_data_for_part_pick(master_id, date, present, service_id),
        )

    if len(present) == 1:
        only = present[0]
        _audit_handled(tenant_id=tenant_id, tool=SHOW_SLOTS_TOOL_SPEC["name"])
        return _build_skill_result(
            text=_slot_prompt(date, only, today, heard=heard),
            tool_calls_made=[],
            confidence=_CONFIDENCE_OK,
            action_data=_action_data_for_slot_pick(
                _slots_in_part(slots, only),
                master_id=master_id,
                service_id=service_id,
            ),
        )

    return _build_skill_result(
        text=_PART_PICK_PROMPT.format(day=day_label(date, today).lower()),
        tool_calls_made=[],
        confidence=_CONFIDENCE_OK,
        action_data=_action_data_for_part_pick(master_id, date, present, service_id),
    )


def _slots_in_part(slots: list, part: str) -> list:
    """Slots of ``slots`` inside ``part``. One definition, one call site each."""
    return [s for s in slots if part_of_iso_datetime(getattr(s, "datetime", "") or "") == part]


def _slot_prompt(date: str, part: str | None, today: Any, *, heard: str = "") -> str:
    """«Завтра вечером — вот что свободно:» / «Выберите время:».

    ``heard`` carries the user's own words when the narrowing came from what
    they SAID rather than from a tap, so the reply shows the request was
    heard instead of quietly acting on it.
    """
    if heard:
        return _HEARD_SLOT_PROMPT.format(heard=heard)
    if part is None:
        return _SLOT_PICK_PROMPT
    return _PART_SLOT_PROMPT.format(day=day_label(date, today), part=PART_PHRASES[part])


def _action_data_for_part_pick(
    master_id: int | str,
    date: str,
    parts: list[str],
    service_id: int | str,
) -> dict[str, Any]:
    """Part-of-day chips + the «Точное время» escape hatch.

    Only parts that HAVE slots on ``date`` are passed in — a chip must lead
    to something, and «Вечер» on a day whose last slot is 15:00 is a button
    into a dead end.
    """
    buttons = [
        {
            "label": f"{PART_CHIP_LABELS[p]} ({PART_RANGE_HINTS[p]})",
            "callback": f"{CALLBACK_BOOK_PICK_PART_PREFIX}{master_id}:{date}:{p}:{service_id}",
        }
        for p in parts
    ]
    buttons.append(
        {
            "label": _LABEL_EXACT_TIME,
            "callback": f"{CALLBACK_BOOK_PICK_PART_PREFIX}{master_id}:{date}:any:{service_id}",
        }
    )
    return {
        "attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}],
        "kind": "part_pick",
        "master_id": master_id,
    }


def _action_data_for_date_pick(
    master_id: int | str,
    dates: list[str],
    service_id: int | str | None = None,
    *,
    today: Any = None,
    collapse: bool = False,
) -> dict[str, Any]:
    """Build the day keyboard from a schedule dates list.

    One button per date, callback
    ``cb:book:pick_date:<master_id>:<YYYY-MM-DD>:<service_id>``.
    Master id and service id are embedded so the date-pick callback stays
    self-contained — no re-fetching context from history.

    DRF-1325: with ``collapse`` the first :data:`_MAX_DAY_CHIPS` days are
    captioned «Сегодня / Завтра / Послезавтра» (relative to ``today`` in the
    SALON's zone, not the server's) and the remainder collapses into a single
    «Выбрать дату» button that re-renders this same keyboard in full. The
    callbacks are unchanged: a chip is the existing date button wearing a
    word a person reads without decoding.
    """
    service_suffix = f":{service_id}" if service_id is not None else ""
    shown = dates[:_MAX_DAY_CHIPS] if collapse else dates
    buttons = [
        {
            "label": day_label(d, today) if today is not None else _date_button_label(d),
            "callback": f"{CALLBACK_BOOK_PICK_DATE_PREFIX}{master_id}:{d}{service_suffix}",
        }
        for d in shown
    ]
    if collapse and len(dates) > len(shown) and service_id is not None:
        buttons.append(
            {
                "label": _LABEL_PICK_DATE,
                "callback": f"{CALLBACK_BOOK_MORE_DATES_PREFIX}{master_id}:{service_id}",
            }
        )
    return {
        "attachments": [
            {
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            }
        ],
        "kind": "date_pick",
        "master_id": master_id,
    }


def _date_button_label(date_str: str) -> str:
    """Readable label for a date button. Format ``📅 22 мая (Ср)``.

    Falls back to the raw ``YYYY-MM-DD`` if parsing fails — UX still
    workable, just less pretty.
    """
    try:
        from datetime import date as _date

        d = _date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return f"📅 {date_str}"
    month = _RU_MONTHS_SHORT[d.month - 1] if 1 <= d.month <= 12 else ""
    wd = _RU_WEEKDAYS_SHORT[d.weekday()] if 0 <= d.weekday() <= 6 else ""
    return f"📅 {d.day} {month} ({wd})".strip()


def _slot_button_label(slot) -> str:
    """One-line readable label for the slot-pick button.

    Format: ``🕐 22 мая (Ср) 14:00``. ISO datetime is parsed defensively;
    if parsing fails the raw datetime string is rendered — UX is still
    workable, just less pretty.
    """
    raw = slot.datetime or ""
    try:
        from datetime import datetime as _dt

        # YClients returns ``YYYY-MM-DDTHH:MM:SS`` — fromisoformat handles it.
        ts = _dt.fromisoformat(raw)
    except (TypeError, ValueError):
        return f"🕐 {raw}"
    day = ts.day
    month = _RU_MONTHS_SHORT[ts.month - 1] if 1 <= ts.month <= 12 else ""
    wd = _RU_WEEKDAYS_SHORT[ts.weekday()] if 0 <= ts.weekday() <= 6 else ""
    return f"🕐 {day} {month} ({wd}) {ts.strftime('%H:%M')}".strip()


def _slots_payload(result: BookingToolResult) -> list[dict[str, Any]] | None:
    if not result.slots:
        return None
    return [{"datetime": s.datetime, "duration_minutes": s.duration_minutes} for s in result.slots]


def _confirmation_payload(result: BookingToolResult) -> dict[str, Any] | None:
    c = result.confirmation
    if c is None or not c.ok:
        return None
    return {
        "record_id": c.record_id,
        "visit_at": c.visit_at,
        "master_name": c.master_name,
        "service_name": c.service_name,
    }


def _pending_payload(result: BookingToolResult) -> dict[str, Any] | None:
    p = result.pending
    if p is None:
        return None
    return {
        "kind": p.kind,
        "preview_text": p.preview_text,
        "token": str(p.token),
    }


def _action_data_for_pending(result: BookingToolResult) -> dict[str, Any] | None:
    """Build the ``SkillResult.action_data`` payload carrying the keyboard.

    Channel adapters consume ``action_data["attachments"]`` to render
    inline keyboards — same shape used by ``cb:rem:*`` reminders
    (see :func:`apps.bookings.tasks._build_attachments`). The
    platform-canonical UI envelope is:

        {"attachments": [{"type": "inline_keyboard",
                          "payload": {"buttons": [...]}}]}

    Two tool families produce keyboards:

    * Preview-gated destructive verbs (confirm / cancel / reschedule)
      — keyboard lives on ``result.pending.keyboard``.
    * ``buy_certificate`` (B7) — keyboard lives on ``result.keyboard``,
      carries a single URL-button to the YooKassa checkout.

    Returns ``None`` when neither is present — keeps the SkillResult
    clean for non-destructive tool paths (show_masters / show_slots /
    show_my_bookings / calc_price).
    """
    if result.pending is not None:
        return {
            "attachments": [
                {
                    "type": "inline_keyboard",
                    "payload": {"buttons": result.pending.keyboard},
                }
            ],
            "pending_action": {
                "kind": result.pending.kind,
                "token": str(result.pending.token),
            },
        }
    if result.keyboard:
        out: dict[str, Any] = {
            "attachments": [
                {
                    "type": "inline_keyboard",
                    "payload": {"buttons": result.keyboard},
                }
            ],
        }
        if result.certificate is not None and result.certificate.ok:
            out["certificate"] = {
                "order_id": result.certificate.order_id,
                "amount_rub": str(result.certificate.amount_rub),
                "checkout_url": result.certificate.checkout_url,
            }
        return out
    return None


def _price_payload(result: BookingToolResult) -> dict[str, Any] | None:
    p = result.price
    if p is None:
        return None
    return {
        "service_name": p.service_name,
        "original_price": str(p.original_price) if p.original_price is not None else None,
        "final_price": str(p.final_price) if p.final_price is not None else None,
        "discount_percent": p.discount_percent,
        "promo_status": p.promo_status,
        # Pre-rendered text — the deterministic fallback. The LLM may
        # rephrase but should preserve the numbers verbatim.
        "rendered_text": result.text,
    }


def _certificate_payload(result: BookingToolResult) -> dict[str, Any] | None:
    """Splice the ``buy_certificate`` payload for the second LLM call.

    The LLM must preserve the amount + the checkout URL verbatim (the
    URL goes into the inline button via ``action_data``). It picks
    the conversational frame based on whether the issuance succeeded.
    """
    cert = result.certificate
    if cert is None:
        return None
    return {
        "ok": cert.ok,
        "order_id": cert.order_id,
        "amount_rub": str(cert.amount_rub),
        "checkout_url": cert.checkout_url,
        "error": cert.error,
        "rendered_text": result.text,
    }


def _bookings_payload(result: BookingToolResult, tool_name: str) -> list[dict[str, Any]] | None:
    if tool_name != SHOW_MY_BOOKINGS_TOOL_SPEC["name"]:
        return None
    return [
        {
            "record_id": b.record_id,
            "visit_at": b.visit_at,
            "master_name": b.master_name,
            "service_name": b.service_name,
        }
        for b in result.bookings
    ]


# ---------------------------------------------------------------------------
# SkillResult builders
# ---------------------------------------------------------------------------


def _build_skill_result(
    *,
    text: str,
    tool_calls_made: list[ToolCall],
    confidence: float | None,
    action_data: dict[str, Any] | None = None,
) -> SkillResult:
    return SkillResult(
        reply_text=text,
        action_type="booking",
        action_data=action_data,
        tool_calls_made=tool_calls_made,
        confidence=confidence,
        meta={"skill": "booking"},
    )


# ── The single exit for a refused booking-callback tap (DRF-1473) ─────────
#
# Refusal reason vocabulary. Locked, like the handoff reasons above, and for
# the same purpose: a name that means one thing is what makes six lines in a
# journal answer «why» without a bisect.
#
#: The payload could not be read — a missing service id, an id that is
#: neither int nor UUID, a datetime that does not parse.
REFUSAL_MALFORMED_CALLBACK = "malformed_callback"
#: The payload named a service the tenant's live catalog does not have.
REFUSAL_UNKNOWN_SERVICE = "unknown_service"
#: The payload named a specialist the tenant's live roster does not have.
#: The pilot defect (DRF-1473) landed here through a truncated roster read.
REFUSAL_UNKNOWN_MASTER = "unknown_master"
#: Master and service passed the pre-checks, then lost a race against a
#: catalog / roster change inside ``confirm_booking``.
REFUSAL_VALIDATION_RACE = "validation_race"
#: The one refusal that is genuinely about time: the tapped slot is past.
REFUSAL_EXPIRED_SLOT = "expired_slot"


def _refuse_callback(
    *,
    step: str,
    reason: str,
    text: str,
    detail: str = "",
) -> SkillResult:
    """Refuse a booking-callback tap, saying the SAME thing twice.

    Once to the person (``text``) and once to the journal (``reason``), so
    the two can never drift apart the way they had before DRF-1473 — where
    five different causes shared one sentence and two of them logged nothing
    at all. ``step`` is the callback being handled (``pick_slot``,
    ``pick_master``…), ``detail`` any ids worth carrying.

    Not a handoff and not an error: these are recoverable locally, exactly
    as before. Only the wording and the log line changed.
    """
    logger.info(
        "booking.%s.refused reason=%s%s",
        step,
        reason,
        f" {detail}" if detail else "",
    )
    return _build_skill_result(text=text, tool_calls_made=[], confidence=_CONFIDENCE_OK)


def _handoff(
    *,
    tool_calls_made: list[ToolCall],
    reason: str,
    text: str,
    tenant_id: str,
) -> SkillResult:
    write_audit(
        EVENT_BOOKING_HANDOFF,
        target="BookingSkill",
        payload={"tenant_id": tenant_id, "reason": reason},
    )
    return SkillResult(
        reply_text=text,
        action_type="booking",
        should_handoff=True,
        handoff_reason=reason,
        tool_calls_made=tool_calls_made,
        meta={"skill": "booking"},
    )


def _audit_handled(*, tenant_id: str, tool: str) -> None:
    write_audit(
        EVENT_BOOKING_HANDLED,
        target="BookingSkill",
        payload={"tenant_id": tenant_id, "tool": tool},
    )


def _load_tenant_master_roster(tenant: Any) -> tuple[list[dict[str, str]], bool]:
    """E0#1 Variant A — load bookable CatalogMaster roster для prompt
    pre-injection. Returns ``(roster, is_truncated)``.

    Per founder verdict 2026-06-02 + memory `pilot_scope_discipline`.
    Read-only against the catalog mirror (per ADR-0009 §Hard rule #2 —
    no cross-repo DB / REST call; mirror staleness ≤15-min is accepted
    SLO). Strictly tenant-scoped via ``all_tenants.filter(tenant=...)``
    — caller may not have a tenant_scope context active depending on
    dispatch path, so the explicit filter is the safety belt.

    **Adversarial CR #955 changes:**

    * **F2** — filter `is_active=True AND invite_status=ACCEPTED`
      matching the model's canonical ``bookable()`` predicate. The
      previous `is_active`-only gate surfaced PENDING / EXPIRED /
      CANCELLED invite masters (M0 invite-flow rows with `is_active=
      True` by default until accepted), creating a false-positive
      roster vs the YClients-grounded ``show_masters`` tool result.
    * **F3** — return a `(roster, is_truncated)` tuple так prompt
      renderer can weaken the «такого мастера нет» rule when the cap
      fired. Without this, alphabetically-late masters get false
      denial. Overflow probe (``[:cap+1]``) is now actually wired —
      previously the +1 row was sliced off without any signal use.
    * **F7** — `specialization` is loaded but NOT surfaced into the
      prompt (renderer drops it per ADR-0011 safety concerns). Loader
      preserves the field shape для backwards-compat с any caller
      that might want it for non-prompt purposes (none today).

    Failure mode: any unexpected DB / ORM exception is logged WARN
    and surfaces as `([], False)`. Pre-injection is defence-in-depth
    — the booking flow still works через the original `show_masters`
    path when the roster block is absent. Critical: this function
    MUST NOT raise (would 500 the customer turn).
    """
    try:
        from apps.catalog.models import CatalogMaster

        rows = list(
            CatalogMaster.all_tenants.filter(
                tenant=tenant,
                is_active=True,
                invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            )
            .order_by("name")
            .values("name", "specialization")[: _KNOWN_MASTERS_ROSTER_CAP + 1]
        )
    except Exception as exc:  # noqa: BLE001 — observability never crashes the turn
        logger.warning(
            "booking.known_masters.load_failed tenant=%s err=%s",
            getattr(tenant, "id", "?"),
            exc,
        )
        return [], False

    if not rows:
        return [], False

    # F3 overflow signal — the +1 probe row, if present, means the
    # alphabetical top-N has truncated the real roster. Surface to
    # the caller so the prompt rule can be relaxed («call show_masters
    # if uncertain» instead of «такого мастера нет»).
    is_truncated = len(rows) > _KNOWN_MASTERS_ROSTER_CAP
    if is_truncated:
        logger.warning(
            "booking.known_masters.capped tenant=%s cap=%d "
            "(at least one master не surfaced — relaxing prompt rule)",
            getattr(tenant, "id", "?"),
            _KNOWN_MASTERS_ROSTER_CAP,
        )

    roster = [
        {
            "name": (row.get("name") or "").strip(),
            "specialization": (row.get("specialization") or "").strip(),
        }
        for row in rows[:_KNOWN_MASTERS_ROSTER_CAP]
        if (row.get("name") or "").strip()
    ]
    return roster, is_truncated


# ---------------------------------------------------------------------------
# Keyword fallback (Sprint-3 callers / tests)
# ---------------------------------------------------------------------------


def _legacy_keyword_match(text: str) -> bool:
    lower = (text or "").lower()
    return any(kw in lower for kw in _BOOKING_KEYWORDS)


# ---------------------------------------------------------------------------
# D-10 — booking-flow continuation state (skill_state["booking_flow"])
# ---------------------------------------------------------------------------


def _read_flow_state(conversation: Any) -> dict[str, Any] | None:
    """Return the active flow state, or None when missing/expired.

    Lazy expiry: stale state is simply not claimed (and NOT deleted
    here — matches() must stay side-effect free); the next write or
    clear overwrites/removes the subkey.
    """
    raw = (getattr(conversation, "skill_state", None) or {}).get(_FLOW_STATE_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        expires_at = datetime.fromisoformat(str(raw.get("expires_at") or ""))
    except ValueError:
        return None
    if expires_at.tzinfo is None:
        expires_at = timezone.make_aware(expires_at)
    if expires_at <= timezone.now():
        return None
    return raw


def _write_flow_state(conversation: Any, *, flow: str, bookings: list[Any]) -> None:
    """Open/refresh the continuation state after a disambiguation reply."""
    from apps.conversations.services import write_skill_state

    state = {
        "flow": flow,
        "stage": _FLOW_STAGE_AWAITING_SELECTION,
        "bookings": [
            {
                "record_id": str(b.record_id),
                "visit_at": b.visit_at,
                "master_name": b.master_name,
                "service_name": b.service_name,
            }
            for b in bookings
        ],
        "expires_at": (timezone.now() + _FLOW_STATE_TTL).isoformat(),
    }
    try:
        write_skill_state(conversation, _FLOW_STATE_KEY, state)
    except Exception:  # noqa: BLE001 — state is an optimization, never crash the turn
        logger.warning(
            "booking.flow.state_write_failed conversation=%s",
            getattr(conversation, "id", "?"),
            exc_info=True,
        )
        # Review finding #5 — without a telemetry row this failure mode
        # is a single log line and the feature silently degrades to
        # pre-D-10 behaviour (e.g. when called outside tenant_scope).
        # ``emit`` is fail-soft; if the root cause IS a missing tenant
        # scope, the Event insert fails too and is swallowed inside
        # emit(). The slug is canonical (review round 2 — a raw string
        # literal also triggered events.emit.non_canonical WARNINGs).
        emit(
            BOOKING_FLOW_STATE_WRITE_FAILED,
            distinct_id="",
            dialog_id=getattr(conversation, "id", None),
            properties={"conversation_id": str(getattr(conversation, "id", ""))},
        )


def _clear_flow_state(conversation: Any) -> None:
    """Remove the continuation state (preview created / user aborted)."""
    if not (getattr(conversation, "skill_state", None) or {}).get(_FLOW_STATE_KEY):
        return
    from apps.conversations.services import write_skill_state

    try:
        write_skill_state(conversation, _FLOW_STATE_KEY, None)
    except Exception:  # noqa: BLE001 — same fail-soft rationale as _write_flow_state
        logger.warning(
            "booking.flow.state_clear_failed conversation=%s",
            getattr(conversation, "id", "?"),
            exc_info=True,
        )


def clear_booking_flow(conversation: Any) -> None:
    """Public wrapper over :func:`_clear_flow_state` for the gate skill.

    The preview gate (``apps.bookings.callbacks``) clears the flow on
    every decision it owns — otherwise a text confirm/cancel resolved
    via the pending-grace window leaves the continuation state alive
    for the rest of its TTL and the booking skill keeps claiming
    selection-shaped turns against a stale disambiguation context
    (review finding #4).
    """
    _clear_flow_state(conversation)


def _maybe_write_flow_state(
    context: SkillContext,
    *,
    message_text: str,
    tool_result: BookingToolResult,
    tenant_id: str,
) -> None:
    """Open the continuation state when a mutation turn lists bookings.

    Fires only when ALL of these hold:
    * the user's own words are a mutation request (reschedule / cancel)
      — read-only lookups («когда моя запись?») never open a flow;
    * the tool returned at least one booking (nothing to select from
      otherwise).
    """
    flow = booking_mutation_flow(message_text)
    if flow is None or not tool_result.bookings:
        return
    _write_flow_state(context.conversation, flow=flow, bookings=tool_result.bookings)
    logger.info(
        "booking.flow.stage tenant_id=%s conversation_id=%s flow=%s stage=%s trace_id=%s",
        tenant_id,
        context.conversation.id,
        flow,
        _FLOW_STAGE_AWAITING_SELECTION,
        context.trace_id,
    )


def _flow_context_payload(
    flow_state: dict[str, Any] | None,
    *,
    query_text: str = "",
) -> dict[str, Any] | None:
    """Phase-1 prompt grounding for a continuation turn (D-10).

    Injected only when the current turn is actually a continuation —
    selection-shaped («первую», «20:00», «на завтра») or confirm-vocab
    («да» replaying a Phase-1 free-text «Подтверждаете?» that never
    created a pending row). A fresh booking request claimed via the
    keyword/lookup fallbacks while a flow happens to be alive must NOT
    get the «АКТИВНЫЙ СЦЕНАРИЙ… НЕ начинай сначала» block (review
    round 2): the stored context belongs to a different request. The
    flow state itself survives — the next selection-shaped turn still
    picks it up.
    """
    if flow_state is None:
        return None
    if not (is_confirm_text(query_text) or looks_like_flow_selection(query_text)):
        return None
    return {
        "flow": flow_state.get("flow"),
        "bookings": flow_state.get("bookings") or [],
        "now_local": timezone.localtime().strftime("%Y-%m-%d %H:%M"),
    }


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
