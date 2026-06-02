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

Services with ``CatalogService.requires_health_check=True`` can't be
booked through the bot without a clinician sign-off. The skill looks
up the service in :mod:`apps.catalog` BEFORE the confirm call and, if
the gate fires, returns ``should_handoff=True`` with reason
``booking_health_check_required``.

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
from typing import Any, ClassVar

from apps.audit.services import write_audit
from apps.bookings.keyboards import (
    CALLBACK_BOOK_PICK_DATE_PREFIX,
    CALLBACK_BOOK_PICK_MASTER_PREFIX,
    CALLBACK_BOOK_PICK_SLOT_PREFIX,
)
from apps.events.services import emit
from apps.events.vocabulary import SKILL_DISPATCHED
from apps.llm.protocol import CompletionResult, LLMError, ToolCall
from apps.skills.base import SkillContext, SkillResult
from apps.skills.booking.prompts import BrandVoiceConfig, build_booking_prompt
from apps.skills.booking.tools import (
    BOOKING_TOOL_SPECS,
    BUY_CERTIFICATE_TOOL_SPEC,
    CALC_PRICE_TOOL_SPEC,
    CANCEL_BOOKING_TOOL_SPEC,
    CONFIRM_BOOKING_TOOL_SPEC,
    RESCHEDULE_BOOKING_TOOL_SPEC,
    SHOW_MASTERS_TOOL_SPEC,
    SHOW_MY_BOOKINGS_TOOL_SPEC,
    SHOW_SLOTS_TOOL_SPEC,
    BookingToolResult,
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
    persona="Алина, администратор салона «Формула тела»",
    tone="дружелюбный, краткий, без канцелярита",
    forbidden=(
        "гарантирую",
        "100%",
        "лучшие в городе",
        "уникальная технология",
    ),
)


_FALLBACK_HANDOFF_TEXT = "Не получилось оформить запись — переключаю на менеджера, он подскажет."

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

# How many dates to render in the picker. YClients usually returns
# a 30-day window; trimming keeps the keyboard tappable on mobile.
_MAX_DATE_BUTTONS = 14


# Audit / event slugs.
EVENT_BOOKING_HANDLED = "booking.handled"
EVENT_BOOKING_HANDOFF = "booking.handoff"


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
        #   * cb:book:pick_master:<staff_id>          — master cards (#505)
        #   * cb:book:pick_date:<master_id>:<date>    — date picker (#517 fixup)
        #   * cb:book:pick_slot:<iso_datetime>        — slot cards (#513)
        text = (context.message_text or "").strip()
        if text.startswith(CALLBACK_BOOK_PICK_MASTER_PREFIX):
            return True
        if text.startswith(CALLBACK_BOOK_PICK_DATE_PREFIX):
            return True
        if text.startswith(CALLBACK_BOOK_PICK_SLOT_PREFIX):
            return True

        intent = context.intent
        if intent is not None:
            return intent.intent == "booking"
        return _legacy_keyword_match(context.message_text)

    def handle(self, context: SkillContext) -> SkillResult:
        from apps.integrations.yclients import get_yclients_client
        from apps.llm.router import get_router

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
            yclients = get_yclients_client()
            services = yclients.get_services()
        except Exception as exc:  # noqa: BLE001
            logger.warning("booking.prefetch.failed err=%s", exc)
            return _handoff(
                tool_calls_made=[],
                reason="booking_yclients_failure",
                text=_FALLBACK_HANDOFF_TEXT,
                tenant_id=tenant_id,
            )

        allowed_service_ids = {int(s.id) for s in services}
        service_lookup = build_service_lookup(services)

        # ── Master-pick callback short-circuit ──────────────────────
        # User tapped a master-card button (cb:book:pick_master:<id>).
        # Fetch the master's available dates and render them as a date
        # picker. show_slots is NOT dispatched here — it would auto-pick
        # the nearest date and surprise the user (UX feedback 2026-05-21:
        # "меня не спросили про дату"). Date-pick callback fires
        # show_slots(master_id, date_from=<date>) on the user's choice.
        text = (context.message_text or "").strip()
        if text.startswith(CALLBACK_BOOK_PICK_MASTER_PREFIX):
            raw_id = text[len(CALLBACK_BOOK_PICK_MASTER_PREFIX) :].strip()
            try:
                master_id = int(raw_id)
            except (TypeError, ValueError):
                logger.warning("booking.pick_master.bad_id raw=%r", raw_id)
                return _build_skill_result(
                    text="Не удалось распознать выбор мастера. Напишите имя ещё раз?",
                    tool_calls_made=[],
                    confidence=None,
                )
            return _render_date_picker(
                master_id=master_id,
                yclients=yclients,
                tenant_id=tenant_id,
            )

        # ── Date-pick callback short-circuit ────────────────────────
        # User tapped a date button (cb:book:pick_date:<master_id>:<date>).
        # Synthesise show_slots(master_id, date_from=<date>) so the
        # existing slot-cards short-circuit picks up + renders times.
        if text.startswith(CALLBACK_BOOK_PICK_DATE_PREFIX):
            payload = text[len(CALLBACK_BOOK_PICK_DATE_PREFIX) :].strip()
            try:
                raw_master, raw_date = payload.split(":", 1)
                master_id = int(raw_master)
            except (TypeError, ValueError):
                logger.warning("booking.pick_date.bad_payload raw=%r", payload)
                return _build_skill_result(
                    text="Не удалось распознать дату. Попробуйте ещё раз.",
                    tool_calls_made=[],
                    confidence=None,
                )
            first = CompletionResult(
                text="",
                tool_calls=[
                    ToolCall(
                        id=f"synth:pick_date:{master_id}:{raw_date}",
                        name=SHOW_SLOTS_TOOL_SPEC["name"],
                        arguments={"master_id": master_id, "date_from": raw_date},
                    )
                ],
                provider="synth",
                finish_reason="tool_calls",
            )
        else:
            # Slot-pick callback (cb:book:pick_slot:<iso_datetime>). Unlike
            # master-pick — which deterministically dispatches show_slots —
            # the slot tap needs the LLM to synthesise confirm_booking
            # with master_id + service_id pulled from short-term history.
            # We override the query with a synthesised user-text so the
            # standard Phase-1 prompt + tool-use loop fires naturally.
            if text.startswith(CALLBACK_BOOK_PICK_SLOT_PREFIX):
                raw_dt = text[len(CALLBACK_BOOK_PICK_SLOT_PREFIX) :].strip()
                if not raw_dt:
                    logger.warning("booking.pick_slot.empty_payload")
                    return _build_skill_result(
                        text="Не удалось распознать время. Напишите ещё раз?",
                        tool_calls_made=[],
                        confidence=None,
                    )
                query_text = f"Запиши меня на {raw_dt}"
            else:
                query_text = context.message_text

            # ── Phase 1: first LLM call (decide on tool use) ───────
            first_messages = build_booking_prompt(
                brand_voice=_DEFAULT_BRAND_VOICE,
                query=query_text,
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

        if handoff_reason:
            return _handoff(
                tool_calls_made=tool_calls_made,
                reason=handoff_reason,
                text=_FALLBACK_HANDOFF_TEXT,
                tenant_id=tenant_id,
            )

        # Master-cards short-circuit: when show_masters returned candidates,
        # render them as a deterministic text + inline-keyboard (one button
        # per master, cb:book:pick_master:<id> payload). Skip Phase 3 LLM
        # to avoid the round-trip + remove the failure mode where the
        # model writes filler text instead of presenting the cards.
        if tool_name == SHOW_MASTERS_TOOL_SPEC["name"] and tool_result.masters:
            _audit_handled(tenant_id=tenant_id, tool=tool_name)
            return _build_skill_result(
                text=_MASTER_PICK_PROMPT,
                tool_calls_made=tool_calls_made,
                confidence=_CONFIDENCE_OK,
                action_data=_action_data_for_master_pick(tool_result.masters),
            )

        # Slot-cards short-circuit (symmetric to master cards). When
        # show_slots returned candidate times, render them as a
        # deterministic text + keyboard (one button per slot,
        # cb:book:pick_slot:<iso_datetime>). Skip Phase 3 LLM —
        # identical UX rationale as master cards.
        if tool_name == SHOW_SLOTS_TOOL_SPEC["name"] and tool_result.slots:
            _audit_handled(tenant_id=tenant_id, tool=tool_name)
            return _build_skill_result(
                text=_SLOT_PICK_PROMPT,
                tool_calls_made=tool_calls_made,
                confidence=_CONFIDENCE_OK,
                action_data=_action_data_for_slot_pick(tool_result.slots),
            )

        # Health-check gate — only relevant for confirm_booking.
        if tool_name == CONFIRM_BOOKING_TOOL_SPEC["name"]:
            service_id = _coerce_int(first_call.arguments.get("service_id"))
            if service_id is not None and _service_requires_health_check(tenant, service_id):
                return _handoff(
                    tool_calls_made=tool_calls_made,
                    reason="booking_health_check_required",
                    text=_FALLBACK_HANDOFF_TEXT,
                    tenant_id=tenant_id,
                )

        # ── Phase 3: second LLM call (natural-language reply) ──────
        second_messages = build_booking_prompt(
            brand_voice=_DEFAULT_BRAND_VOICE,
            query=context.message_text,
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
    allowed_service_ids: set[int],
    service_lookup: dict[int, str],
    tenant_id: str,
) -> tuple[BookingToolResult, str]:
    """Run the chosen tool. Returns ``(result, handoff_reason_or_empty)``."""
    if tool_name == SHOW_MASTERS_TOOL_SPEC["name"]:
        result = show_masters(client=yclients, arguments=arguments, tenant_id=tenant_id)
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


def _fetch_master_ids(yclients: Any) -> set[int]:
    try:
        return {int(s.id) for s in yclients.get_staff(staff_id=None)}
    except Exception:  # noqa: BLE001 — defensive; caller handles handoff
        return set()


def _fetch_master_lookup(yclients: Any) -> dict[int, str]:
    try:
        return build_master_lookup(yclients.get_staff(staff_id=None))
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Health-check gate
# ---------------------------------------------------------------------------


def _service_requires_health_check(tenant: Any, service_id: int) -> bool:
    """Lookup :class:`CatalogService.requires_health_check` for ``service_id``.

    Catalog mirror is the source of truth. If we can't find the service
    row (e.g. catalog isn't synced yet), we DEFAULT to ``False`` — better
    UX to attempt the booking than to dead-end every flow.
    """
    try:
        from apps.catalog.models import CatalogService
    except ImportError:  # pragma: no cover — catalog always available
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


def _action_data_for_master_pick(masters: list) -> dict[str, Any]:
    """Build the master-cards keyboard from a show_masters result.

    Channel adapters consume the platform-canonical envelope shape (same
    as preview-gated confirms in :func:`_action_data_for_pending`). The
    handler ``_build_attachments`` converts the channel-agnostic
    ``[{label, callback}]`` list to the MAX wire format.

    One button per master, callback payload
    ``cb:book:pick_master:<staff_id>``. The skill's matches() picks the
    prefix up on the tap and the handle() short-circuit dispatches
    show_slots(master_id=<id>) without a Phase 1 LLM round-trip.

    Label shape: ``<emoji> <name>`` with a specialisation hint when
    distinct from the canonical "Мастер массажа" boilerplate — keeps the
    button readable in MAX's tight inline-keyboard layout.
    """
    buttons = [
        {
            "label": _master_button_label(m),
            "callback": f"{CALLBACK_BOOK_PICK_MASTER_PREFIX}{m.id}",
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


def _action_data_for_slot_pick(slots: list) -> dict[str, Any]:
    """Build the slot-cards keyboard from a show_slots result.

    Mirror of :func:`_action_data_for_master_pick`. One button per slot,
    callback payload ``cb:book:pick_slot:<iso_datetime>`` carrying the
    YClients-returned ISO datetime verbatim. The skill's slot-callback
    branch synthesises a "запиши меня на <datetime>" user-text so the
    Phase-1 LLM can emit confirm_booking with master_id + service_id
    drawn from short-term conversation memory.
    """
    buttons = [
        {
            "label": _slot_button_label(s),
            "callback": f"{CALLBACK_BOOK_PICK_SLOT_PREFIX}{s.datetime}",
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


def _render_date_picker(
    *,
    master_id: int,
    yclients: Any,
    tenant_id: str,
) -> SkillResult:
    """Build the date-picker SkillResult after a master pick.

    Calls ``client.get_available_dates`` directly (no tool wrapper —
    the dates list isn't an LLM-grounded artefact, it's pure ops data).
    Renders the first :data:`_MAX_DATE_BUTTONS` dates as inline-keyboard
    buttons with ``cb:book:pick_date:<master_id>:<YYYY-MM-DD>`` payloads.

    YClients failures fold into the same handoff path as the rest of
    the booking flow — UX is "переключаю на менеджера" rather than a
    raw error.
    """
    from apps.integrations.yclients import YClientsAPIError, YClientsUnavailableError

    try:
        dates = yclients.get_available_dates(staff_id=master_id, service_ids=None)
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

    capped = sorted(dates)[:_MAX_DATE_BUTTONS]
    return _build_skill_result(
        text=_DATE_PICK_PROMPT,
        tool_calls_made=[],
        confidence=_CONFIDENCE_OK,
        action_data=_action_data_for_date_pick(master_id, capped),
    )


def _action_data_for_date_pick(master_id: int, dates: list[str]) -> dict[str, Any]:
    """Build the date-cards keyboard from a YClients dates list.

    One button per date, callback ``cb:book:pick_date:<master_id>:<YYYY-MM-DD>``.
    Master id is embedded so the date-pick callback can synthesise
    show_slots without re-fetching the master from history.
    """
    buttons = [
        {
            "label": _date_button_label(d),
            "callback": f"{CALLBACK_BOOK_PICK_DATE_PREFIX}{master_id}:{d}",
        }
        for d in dates
    ]
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


# ---------------------------------------------------------------------------
# Keyword fallback (Sprint-3 callers / tests)
# ---------------------------------------------------------------------------


def _legacy_keyword_match(text: str) -> bool:
    lower = (text or "").lower()
    return any(kw in lower for kw in _BOOKING_KEYWORDS)


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
