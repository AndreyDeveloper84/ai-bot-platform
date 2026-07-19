"""Booking-skill tool specs + handlers (DRF-839 / Phase 1 / B3 + B5 + B6).

Seven LLM-callable functions:

* :func:`show_masters` — list staff for a service.
* :func:`show_slots` — list time slots for a master.
* :func:`confirm_booking` — preview-only since B5; renders a 2-button
  confirmation card + stores a :class:`PendingBookingAction` row. The
  actual YClients record is created when the user taps ✅ — see
  :func:`execute_confirm`.
* :func:`cancel_booking` — preview-only; same gate as confirm. The
  YClients cancel + ``BookingRequest.status`` flip happens in
  :func:`execute_cancel` on the ✅ tap.
* :func:`reschedule_booking` — preview-only; validates the new slot
  is available BEFORE prompting. The cancel-and-recreate is done in
  :func:`execute_reschedule` on the ✅ tap (YClients has no native
  reschedule).
* :func:`show_my_bookings` — list the bot_user's upcoming bookings.
* :func:`calc_price` (B6 / DRF-842) — quote a service price with an
  optional promo code. Read-only; never touches YClients (price data
  is mirrored on :class:`apps.catalog.models.CatalogService`).

Each tool spec follows the OpenAI ``{name, description, parameters}``
shape (L1 canonical form, same as
:data:`apps.skills.faq.tools.SEARCH_KB_TOOL_SPEC`); the L3 router wraps
for SDK-specific envelopes at call time.

### Anti-hallucination

The LLM is forbidden from inventing IDs. Each tool that consumes a
``master_id`` / ``service_id`` / ``record_id`` validates it against the
staff/service list (or the bot_user's own ``BookingRequest`` rows for
``record_id`` in cancel/reschedule) before doing anything destructive.
The skill caller pre-fetches the staff + services lists; the
record_id allow-set is fetched per-call from ``BookingRequest``.

### Two-step confirmation gate (B5 / absorbs B4 / DRF-840)

All three destructive verbs (``confirm`` / ``cancel`` / ``reschedule``)
emit a preview-only :class:`BookingToolResult` whose ``pending`` field
carries:

* the keyboard payload (2 buttons: ✅ Подтверждаю / ❌ Отмена)
* an opaque token (a :class:`PendingBookingAction.id` UUID)
* the rendered preview text (which the LLM rephrases in the second
  call before sending)

Tapping ✅ routes through :mod:`apps.bookings.callbacks` to one of the
``execute_*`` functions in this module. Tapping ❌ marks the pending
row consumed; no destructive call. Expiration (10 min) returns a
polite "слишком много времени прошло" reply.

### Idempotency

:func:`execute_confirm` looks up an existing
:class:`BookingRequest` row whose ``comment`` carries the
``yclients_record_id=...`` marker before creating a new one — mirrors
:mod:`apps.integrations.yclients.webhooks` admin-path scheme. The
``PendingBookingAction`` CAS on ``consumed_at`` is the first-level
deduplication; the comment-marker lookup is the second-level safety
net for the (theoretically impossible) case where the CAS races
across two database connections.

### Reschedule as cancel-and-create

YClients has no native reschedule endpoint. The legacy mysite flow
(prod-validated) is "cancel old record, create new record at new
datetime". The partial-failure window (cancel succeeds, create fails)
is unavoidable; per spec, we set the old ``BookingRequest`` to
``cancelled``, emit a handoff with reason
``"booking_reschedule_partial_failure"``, and notify the manager so
they can rebook by hand. We DO NOT retry the create — risk of double-
booking dwarfs the inconvenience of one handoff.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from collections.abc import Set as AbstractSet
from typing import Any, Literal
from uuid import UUID

from django.utils import timezone as dj_timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingRequest, PendingBookingAction
from apps.booking.services.attribution import (
    CommercialIdentitySnapshot,
    build_customer_attribution_metadata,
    build_live_commercial_identity,
    compute_assist_score,
    compute_billable,
    get_reschedulable_statuses,
)
from apps.eventbus import services as eventbus_services
from apps.bookings.keyboards import confirm_2_button
from apps.bookings.pending_actions import create_pending
from apps.bookings.reminders_factory import create_reminders_for_booking
from apps.integrations.yclients import (
    AvailableTime,
    BookingRecord,
    Service,
    Staff,
    YClientsAPIError,
    YClientsUnavailableError,
)
from apps.skills.booking.provider import YClientsSpecialistUnavailableError
from apps.promotions.formatting import format_rub
from apps.promotions.services import validate_promo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool specs (OpenAI / L1 canonical shape)
# ---------------------------------------------------------------------------


SHOW_MASTERS_TOOL_SPEC: dict[str, Any] = {
    "name": "show_masters",
    "description": (
        "List salon masters (staff) who offer a given service. Call when "
        "the user names a service or asks 'кто работает с ...' / 'к кому "
        "записаться на ...'. Either service_id (when known from the "
        "catalog) or service_name (free-text from the user) MUST be set."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service_id": {
                "type": "integer",
                "description": "YClients service id, when known.",
            },
            "service_name": {
                "type": "string",
                "description": "Free-text service description from the user.",
            },
        },
        "required": [],
    },
}


SHOW_SLOTS_TOOL_SPEC: dict[str, Any] = {
    "name": "show_slots",
    "description": (
        "List available time slots for a master. Call AFTER show_masters "
        "so master_id is grounded in a real staff row. date_from is an "
        "optional ISO date (YYYY-MM-DD) — defaults to the next bookable "
        "date YClients offers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "master_id": {
                "type": "integer",
                "description": "YClients staff id, must come from a prior show_masters call.",
            },
            "service_id": {
                "type": "integer",
                "description": "YClients service id, optional but improves accuracy.",
            },
            "date_from": {
                "type": "string",
                "description": "ISO date (YYYY-MM-DD). Optional — first available used otherwise.",
            },
        },
        "required": ["master_id"],
    },
}


CONFIRM_BOOKING_TOOL_SPEC: dict[str, Any] = {
    "name": "confirm_booking",
    "description": (
        "Show a 2-button confirmation card BEFORE creating a YClients "
        "booking. Call when the user picked a master + slot. Does NOT "
        "actually create the record — the real create fires when the "
        "user taps ✅ on the card. master_id and service_id MUST come "
        "from prior show_masters / show_slots calls — never guess. "
        "slot_datetime is the ISO datetime YClients returned in "
        "show_slots. client_phone defaults to the bot_user's stored "
        "phone."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "master_id": {
                "type": "integer",
                "description": "YClients staff id, grounded by show_masters.",
            },
            "service_id": {
                "type": "integer",
                "description": "YClients service id, grounded by show_masters.",
            },
            "slot_datetime": {
                "type": "string",
                "description": "ISO datetime from show_slots, e.g. 2026-05-20T14:30:00.",
            },
            "client_phone": {
                "type": "string",
                "description": "Optional override phone. Defaults to bot_user.phone.",
            },
        },
        "required": ["master_id", "service_id", "slot_datetime"],
    },
}


CANCEL_BOOKING_TOOL_SPEC: dict[str, Any] = {
    "name": "cancel_booking",
    "description": (
        "Show a 2-button confirmation card BEFORE cancelling an "
        "existing booking. Call when the user asks 'отмени мою запись' "
        "or similar. Does NOT cancel until the user taps ✅. record_id "
        "MUST belong to the current user (matched against their own "
        "BookingRequest rows) — never invent. Optional reason text "
        "gets appended to the booking's comment for the salon's "
        "follow-up review."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "record_id": {
                "type": "integer",
                "description": (
                    "YClients record id of the booking to cancel. "
                    "MUST come from show_my_bookings — must belong to "
                    "the current user. Never invent."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Free-text reason (optional). Appended to comment.",
            },
        },
        "required": ["record_id"],
    },
}


RESCHEDULE_BOOKING_TOOL_SPEC: dict[str, Any] = {
    "name": "reschedule_booking",
    "description": (
        "Show a 2-button confirmation card BEFORE rescheduling a "
        "booking. YClients has no native reschedule, so the actual "
        "implementation is cancel-and-create. Call when the user "
        "asks to move an existing booking to a new time. record_id "
        "MUST belong to the current user (matched against their own "
        "BookingRequest rows). new_datetime MUST be a future slot "
        "the master actually has free — the tool validates against "
        "get_available_times before showing the card."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "record_id": {
                "type": "integer",
                "description": (
                    "YClients record id of the booking to move. MUST come "
                    "from show_my_bookings — must belong to the user."
                ),
            },
            "new_datetime": {
                "type": "string",
                "description": (
                    "ISO datetime to move to, e.g. 2026-05-22T10:00:00. "
                    "Must be a future time the master has free."
                ),
            },
        },
        "required": ["record_id", "new_datetime"],
    },
}


SHOW_MY_BOOKINGS_TOOL_SPEC: dict[str, Any] = {
    "name": "show_my_bookings",
    "description": (
        "List the user's upcoming bookings (visit_at >= now, status "
        "CONFIRMED). Call when the user asks 'мои записи', 'когда у меня "
        "запись', or similar. No arguments — scoped to the current "
        "bot_user automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


CALC_PRICE_TOOL_SPEC: dict[str, Any] = {
    "name": "calc_price",
    "description": (
        "Quote the price of a service, optionally applying a promo code. "
        "Call when the client asks about a price ('сколько стоит ...'), "
        "or mentions a promo code ('у меня промокод ...'). Pass "
        "promo_code ONLY if the client explicitly named one — never "
        "invent. service_id MUST come from the bot's known catalog "
        "(prior show_masters / show_slots, or the visible service "
        "list); fabricated IDs trigger a handoff."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service_id": {
                "type": "integer",
                "description": (
                    "Catalog/YClients service id. Must come from the "
                    "bot's known catalog — never invent."
                ),
            },
            "promo_code": {
                "type": "string",
                "description": (
                    "Promo code the client mentioned. Optional. Pass "
                    "only when the client explicitly named one."
                ),
            },
        },
        "required": ["service_id"],
    },
}


BUY_CERTIFICATE_TOOL_SPEC: dict[str, Any] = {
    "name": "buy_certificate",
    "description": (
        "Request a hosted-checkout URL for a gift certificate purchase "
        "from the Ayla payments service. Call when the client asks to "
        "buy a сертификат / подарочный сертификат. amount_rub MUST be "
        "a positive number in [500, 100000] roubles — out-of-range "
        "values trigger a polite clarification. recipient_name is the "
        "person the certificate is for (free-text, optional — blank "
        "means 'for me'). buyer_email is optional; supply it ONLY if "
        "the client volunteers an email for the receipt. The tool "
        "returns a checkout URL — render it with an inline "
        "'💳 Оплатить' button so the client can pay."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "amount_rub": {
                "type": "number",
                "description": (
                    "Certificate amount in roubles. Must be in "
                    "[500, 100000] — values outside trigger a "
                    "clarification."
                ),
            },
            "recipient_name": {
                "type": "string",
                "description": (
                    "Whom the certificate is for. Optional — empty "
                    "means the buyer is also the recipient."
                ),
            },
            "buyer_email": {
                "type": "string",
                "description": (
                    "Buyer's email for the receipt. Optional — only "
                    "pass when the client explicitly named one."
                ),
            },
        },
        "required": ["amount_rub"],
    },
}


BOOKING_TOOL_SPECS: list[dict[str, Any]] = [
    SHOW_MASTERS_TOOL_SPEC,
    SHOW_SLOTS_TOOL_SPEC,
    CONFIRM_BOOKING_TOOL_SPEC,
    CANCEL_BOOKING_TOOL_SPEC,
    RESCHEDULE_BOOKING_TOOL_SPEC,
    SHOW_MY_BOOKINGS_TOOL_SPEC,
    CALC_PRICE_TOOL_SPEC,
    BUY_CERTIFICATE_TOOL_SPEC,
]


def get_active_booking_tool_specs() -> list[dict[str, Any]]:
    """Return the booking-tool list filtered by feature flags.

    Stabilization sprint B2: ``BUY_CERTIFICATE_TOOL_SPEC`` is hidden
    from the LLM tool advertisement when ``CERTIFICATE_PAYMENT_ENABLED``
    is False, so the model does not pitch certificates we cannot
    fulfil. The direct ``buy_certificate()`` call also honours the
    flag for defence-in-depth.
    """

    from django.conf import settings

    if getattr(settings, "CERTIFICATE_PAYMENT_ENABLED", False):
        return list(BOOKING_TOOL_SPECS)
    return [s for s in BOOKING_TOOL_SPECS if s is not BUY_CERTIFICATE_TOOL_SPEC]


# Audit slugs.
EVENT_BOOKING_TOOL_INVOKED = "booking.tool_invoked"
EVENT_BOOKING_CONFIRMED = "booking.confirmed"
EVENT_BOOKING_CONFIRM_FAILED = "booking.confirm_failed"
EVENT_BOOKING_PREVIEW = "booking.preview"
EVENT_BOOKING_CANCELLED = "booking.cancelled"
EVENT_BOOKING_CANCEL_FAILED = "booking.cancel_failed"
EVENT_BOOKING_RESCHEDULED = "booking.rescheduled"
EVENT_BOOKING_RESCHEDULE_PARTIAL = "booking.reschedule_partial_failure"
EVENT_BOOKING_PRICE_QUOTED = "booking.price_quoted"
EVENT_CERTIFICATE_CHECKOUT_REQUESTED = "booking.certificate_checkout_requested"
EVENT_CERTIFICATE_CHECKOUT_FAILED = "booking.certificate_checkout_failed"


# B7 / DRF-843 — amount bounds for buy_certificate. Enforced at the
# tool layer; the DB column is unconstrained because admin paths must
# remain able to fix up edge cases.
CERTIFICATE_AMOUNT_MIN = Decimal("500")
CERTIFICATE_AMOUNT_MAX = Decimal("100000")


# B6 / DRF-842 — promo_status values reported by ``calc_price``.
# Adds ``not_supplied`` on top of the validate_promo taxonomy.
PromoStatus = Literal[
    "ok",
    "not_found",
    "inactive",
    "not_started",
    "expired",
    "wrong_service",
    "not_supplied",
]


# ---------------------------------------------------------------------------
# DTOs returned to the LLM (serialisable, no Django models)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MasterCandidate:
    """Trimmed staff DTO for the LLM tool result."""

    id: int
    name: str
    specialization: str
    photo_url: str
    score: float


@dataclass(frozen=True)
class SlotCandidate:
    """Trimmed time-slot DTO for the LLM tool result."""

    datetime: str  # ISO8601
    duration_minutes: int


@dataclass(frozen=True)
class ConfirmationResult:
    """Return value of :func:`confirm_booking`.

    On success ``ok=True`` and the record fields are populated. On a
    YClients failure ``ok=False``, ``error`` carries a short slug the
    skill maps onto :attr:`SkillResult.handoff_reason`.
    """

    ok: bool
    record_id: int | str = 0
    visit_at: str = ""
    master_name: str = ""
    service_name: str = ""
    error: str = ""


@dataclass(frozen=True)
class BookingRow:
    """Trimmed booking DTO for :func:`show_my_bookings`."""

    record_id: int | str
    visit_at: str
    master_name: str
    service_name: str
    status: str


@dataclass(frozen=True)
class PendingPreview:
    """Preview-card DTO emitted by the destructive-verb tools.

    B5 / DRF-841 (absorbs B4 / DRF-840). Three tools — ``confirm_booking``
    / ``cancel_booking`` / ``reschedule_booking`` — return this instead
    of executing immediately. The skill stamps the keyboard into the
    SkillResult ``action_data`` so the channel adapter renders the
    2-button card; the user's tap routes to one of the ``execute_*``
    functions through :mod:`apps.bookings.callbacks`.

    Fields:

    * ``kind``     — ``confirm`` / ``cancel`` / ``reschedule`` so the
                    callback handler knows which executor to call.
    * ``token``    — UUID pk of the :class:`PendingBookingAction` row
                    (also embedded in the callback payload).
    * ``preview_text`` — pre-rendered Russian summary of what's about
                    to happen. The skill's second LLM call can rephrase
                    or the deterministic fallback uses this verbatim.
    * ``keyboard`` — the platform-canonical ``list[{label, callback}]``
                    button row; channel adapter renders per-platform.
    """

    kind: str
    token: UUID
    preview_text: str
    keyboard: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class CalcPriceResult:
    """Return value of :func:`calc_price` (B6 / DRF-842).

    Fields:

    * ``service_name``     — display name from catalog (snapshot of the
                            catalog row at quote time).
    * ``original_price``   — Decimal from
                            :attr:`apps.catalog.models.CatalogService.price_from`.
                            ``None`` when the service has no base price
                            ("по запросу" услуги).
    * ``final_price``      — Original minus discount when ``promo_status="ok"``;
                            equal to original otherwise. ``None`` mirrors
                            ``original_price=None``.
    * ``discount_percent`` — int in 1..99 when ``promo_status="ok"``;
                            ``None`` otherwise.
    * ``promo_status``     — see :data:`PromoStatus`. ``"not_supplied"``
                            is the no-promo-mentioned case; the seven
                            others mirror :data:`apps.promotions.services.PromoReason`.
    """

    service_name: str
    original_price: Decimal | None
    final_price: Decimal | None
    discount_percent: int | None
    promo_status: PromoStatus


@dataclass(frozen=True)
class BuyCertificateResult:
    """Return value of :func:`buy_certificate` (B7 / DRF-843).

    Fields:

    * ``ok``           — True on a successful checkout-URL issuance.
    * ``order_id``     — Ayla payment id (#427 carve-out: the DTO
                         field name is unchanged for downstream
                         render-layer compatibility, but the value is
                         now Ayla djangoproject's canonical payment_id
                         since bot-platform no longer owns an Order
                         row per ADR-0009 §Hard rule #1). Empty on
                         failure paths.
    * ``amount_rub``   — Echoed for downstream rendering. ``Decimal("0")``
                         on failures.
    * ``checkout_url`` — YooKassa-hosted checkout URL Ayla obtained on
                         our behalf (stub URL in test mode). Empty on
                         failures.
    * ``error``        — Slug used by the skill to map onto a handoff
                         reason. Empty on success.
    """

    ok: bool
    order_id: str = ""
    amount_rub: Decimal = field(default_factory=lambda: Decimal("0"))
    checkout_url: str = ""
    error: str = ""


@dataclass
class BookingToolResult:
    """Container the skill stores between LLM calls.

    Holds the master + slot allow-sets so the LLM cannot inject a fake
    ID in the second LLM call. Always carries the rendered text + a
    structured payload for telemetry.
    """

    text: str = ""
    masters: list[MasterCandidate] = field(default_factory=list)
    slots: list[SlotCandidate] = field(default_factory=list)
    confirmation: ConfirmationResult | None = None
    bookings: list[BookingRow] = field(default_factory=list)
    pending: PendingPreview | None = None
    price: CalcPriceResult | None = None
    certificate: BuyCertificateResult | None = None
    keyboard: list[dict[str, str]] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# Tool 1: show_masters
# ---------------------------------------------------------------------------


def show_masters(
    *,
    client: Any,
    arguments: dict[str, Any],
    tenant_id: str,
) -> BookingToolResult:
    """List masters who can perform the requested service.

    Args:
      client: The YClients HTTP client (B1).
      arguments: LLM-supplied ``{service_id?, service_name?}``.
      tenant_id: tenant scope for audit only.

    Returns a :class:`BookingToolResult` with ``masters`` populated.
    Empty list is a valid (non-error) result — the skill turns it into
    a handoff at the call site.
    """
    service_id = _coerce_id(arguments.get("service_id"))
    service_name = str(arguments.get("service_name") or "").strip()

    try:
        staff_rows = client.get_staff(staff_id=None)
    except YClientsUnavailableError as exc:
        logger.warning("booking.show_masters.unavailable err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="show_masters", outcome="unavailable")
        return BookingToolResult(error="yclients_unavailable")
    except YClientsAPIError as exc:
        logger.info("booking.show_masters.api_error err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="show_masters", outcome="api_error")
        return BookingToolResult(error="yclients_api_error")

    scored: list[MasterCandidate] = []
    needle = service_name.lower()
    for staff in staff_rows:
        score = _relevance_score(staff, needle, service_id)
        scored.append(
            MasterCandidate(
                id=staff.id,
                name=staff.name,
                specialization=staff.specialization,
                photo_url=staff.avatar,
                score=score,
            )
        )

    # Stable ordering: highest score first, then by name.
    scored.sort(key=lambda m: (-m.score, m.name))

    _audit_tool(
        tenant_id=tenant_id,
        tool="show_masters",
        outcome="ok",
        extra={"count": len(scored)},
    )
    text = _format_masters_text(scored)
    return BookingToolResult(text=text, masters=scored)


def _relevance_score(staff: Staff, needle: str, service_id: int | str | None) -> float:
    """Cheap textual relevance — keeps the LLM grounded without RAG.

    A real solution would join through ``CatalogService`` ↔ master
    relations from :mod:`apps.catalog`; Sprint 11+ scope. Until then we
    score by substring match on specialization / name vs the user
    query.
    """
    if service_id is not None:
        # Without a catalog join we can't filter; downgrade to a flat
        # neutral score so all staff stay in the list.
        return 0.5
    if not needle:
        return 0.5
    haystack = f"{staff.specialization} {staff.name} {staff.position}".lower()
    return 1.0 if needle in haystack else 0.2


def _format_masters_text(masters: list[MasterCandidate]) -> str:
    if not masters:
        return "Не нашла подходящих мастеров для этой услуги."
    lines = ["Вот наши мастера:"]
    for m in masters[:5]:
        spec = f" — {m.specialization}" if m.specialization else ""
        lines.append(f"• {m.name}{spec}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: show_slots
# ---------------------------------------------------------------------------


def show_slots(
    *,
    client: Any,
    arguments: dict[str, Any],
    tenant_id: str,
    allowed_master_ids: AbstractSet[int | str],
) -> BookingToolResult:
    """List available time slots for ``master_id``.

    Validates ``master_id`` against ``allowed_master_ids`` (the set
    populated by a prior :func:`show_masters` call). Returns
    ``error="invalid_master_id"`` when the LLM hallucinated.
    """
    master_id = _coerce_id(arguments.get("master_id"))
    if master_id is None or master_id not in allowed_master_ids:
        logger.info(
            "booking.show_slots.invalid_master_id master_id=%s allowed=%s",
            master_id,
            sorted(allowed_master_ids),
        )
        _audit_tool(tenant_id=tenant_id, tool="show_slots", outcome="invalid_master_id")
        return BookingToolResult(error="invalid_master_id")

    service_id = _coerce_id(arguments.get("service_id"))
    service_ids = [service_id] if service_id is not None else None

    date_from = str(arguments.get("date_from") or "").strip()

    try:
        dates = client.get_available_dates(
            staff_id=master_id,
            service_ids=service_ids,
        )
    except YClientsUnavailableError:
        _audit_tool(tenant_id=tenant_id, tool="show_slots", outcome="unavailable")
        return BookingToolResult(error="yclients_unavailable")
    except YClientsAPIError:
        _audit_tool(tenant_id=tenant_id, tool="show_slots", outcome="api_error")
        return BookingToolResult(error="yclients_api_error")

    target_date = _pick_target_date(dates, date_from)
    if target_date is None:
        _audit_tool(tenant_id=tenant_id, tool="show_slots", outcome="no_dates")
        return BookingToolResult(text="Нет свободных дат у мастера в ближайшее время.")

    try:
        times = client.get_available_times(
            staff_id=master_id,
            date=target_date,
            service_ids=service_ids,
        )
    except YClientsUnavailableError:
        _audit_tool(tenant_id=tenant_id, tool="show_slots", outcome="unavailable")
        return BookingToolResult(error="yclients_unavailable")
    except YClientsAPIError:
        _audit_tool(tenant_id=tenant_id, tool="show_slots", outcome="api_error")
        return BookingToolResult(error="yclients_api_error")

    slots: list[SlotCandidate] = []
    for t in times:
        candidate = _to_slot_candidate(t, target_date)
        if candidate is not None:
            slots.append(candidate)

    _audit_tool(
        tenant_id=tenant_id,
        tool="show_slots",
        outcome="ok",
        extra={"date": target_date, "count": len(slots)},
    )
    text = _format_slots_text(slots, target_date)
    return BookingToolResult(text=text, slots=slots)


def _pick_target_date(dates: list[str], date_from: str) -> str | None:
    if not dates:
        return None
    if date_from:
        for d in dates:
            if d >= date_from:
                return d
    return dates[0]


def _to_slot_candidate(slot: AvailableTime, target_date: str) -> SlotCandidate | None:
    if slot.datetime:
        iso = slot.datetime
    elif slot.time:
        iso = f"{target_date}T{slot.time}:00"
    else:
        return None
    duration = (slot.seance_length_s or 0) // 60
    return SlotCandidate(datetime=iso, duration_minutes=duration)


def _format_slots_text(slots: list[SlotCandidate], target_date: str) -> str:
    if not slots:
        return f"На {target_date} свободных слотов нет."
    lines = [f"Свободные слоты на {target_date}:"]
    for s in slots[:8]:
        # Pull HH:MM out of the ISO datetime for compact rendering.
        time_part = s.datetime.split("T", 1)[1][:5] if "T" in s.datetime else s.datetime
        lines.append(f"• {time_part}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 3: confirm_booking
# ---------------------------------------------------------------------------


def confirm_booking(
    *,
    client: Any,
    arguments: dict[str, Any],
    tenant: Any,
    bot_user: Any,
    allowed_master_ids: AbstractSet[int | str],
    allowed_service_ids: AbstractSet[int | str],
    master_lookup: dict[Any, str],
    service_lookup: dict[Any, str],
) -> BookingToolResult:
    """Preview the booking, persist a :class:`PendingBookingAction`, return card.

    Args:
      client: B1 YClients client. UNUSED by the preview path (kept in
        the signature so the skill-side dispatch is symmetric across the
        six tools) — :func:`execute_confirm` is what actually talks to
        YClients on the ✅ tap.
      arguments: LLM-supplied ``{master_id, service_id, slot_datetime, client_phone?}``.
      tenant: current :class:`Tenant`.
      bot_user: current :class:`BotUser`.
      allowed_master_ids: validated set from prior :func:`show_masters`.
      allowed_service_ids: validated set from pre-fetched services list.
      master_lookup / service_lookup: id → display-name maps for the
        preview text + the snapshot stored on the pending row.

    Returns a :class:`BookingToolResult` with ``pending`` populated.
    The skill stamps the keyboard into ``SkillResult.action_data``.

    B5 / DRF-841 (absorbs B4 / DRF-840): this used to do the actual
    YClients create + ``BookingRequest`` insert in one shot. Now it
    just validates inputs, persists the pending action, and returns
    the preview card. :func:`execute_confirm` runs on the ✅ tap.
    """
    master_id = _coerce_id(arguments.get("master_id"))
    service_id = _coerce_id(arguments.get("service_id"))
    slot_datetime = str(arguments.get("slot_datetime") or "").strip()
    client_phone = str(arguments.get("client_phone") or "").strip()

    tenant_id = str(getattr(tenant, "id", ""))
    _ = client  # explicitly unused on the preview path; see docstring

    if master_id is None or master_id not in allowed_master_ids:
        _audit_tool(tenant_id=tenant_id, tool="confirm_booking", outcome="invalid_master_id")
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="invalid_master_id"),
            error="invalid_master_id",
        )
    if service_id is None or service_id not in allowed_service_ids:
        _audit_tool(tenant_id=tenant_id, tool="confirm_booking", outcome="invalid_service_id")
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="invalid_service_id"),
            error="invalid_service_id",
        )
    if not slot_datetime:
        _audit_tool(tenant_id=tenant_id, tool="confirm_booking", outcome="missing_slot")
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="missing_slot"),
            error="missing_slot",
        )

    phone = client_phone or getattr(bot_user, "phone", "") or ""
    client_name = (
        getattr(bot_user, "client_name", "") or getattr(bot_user, "display_name", "") or "Client"
    )
    master_name = master_lookup.get(master_id, "")
    service_name = service_lookup.get(service_id, "")

    payload = {
        "master_id": master_id,
        "service_id": service_id,
        "slot_datetime": slot_datetime,
        "client_phone": phone,
        "client_name": client_name,
        "master_name": master_name,
        "service_name": service_name,
    }
    token = create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.CONFIRM,
        payload=payload,
    )

    preview_text = _format_confirm_preview(
        master_name=master_name,
        service_name=service_name,
        slot_datetime=slot_datetime,
    )
    keyboard = confirm_2_button(str(token))

    _audit_tool(tenant_id=tenant_id, tool="confirm_booking", outcome="preview")
    write_audit(
        EVENT_BOOKING_PREVIEW,
        target="BookingSkill",
        payload={
            "tenant_id": tenant_id,
            "kind": "confirm",
            "token": str(token),
        },
    )
    return BookingToolResult(
        text=preview_text,
        pending=PendingPreview(
            kind=PendingBookingAction.Kind.CONFIRM,
            token=token,
            preview_text=preview_text,
            keyboard=keyboard,
        ),
    )


def _format_confirm_preview(
    *,
    master_name: str,
    service_name: str,
    slot_datetime: str,
) -> str:
    """Render the Russian preview body for a ``confirm_booking`` card.

    Wording matches the salon brand voice (warm, compact, no
    boilerplate). The second LLM call in :class:`BookingSkill` MAY
    rephrase; this is the deterministic fallback the channel adapter
    uses if the LLM returns empty.
    """
    parts = ["Записываю:"]
    if service_name:
        parts.append(f"• Услуга: {service_name}")
    if master_name:
        parts.append(f"• Мастер: {master_name}")
    if slot_datetime:
        parts.append(f"• Время: {slot_datetime}")
    parts.append("Подтверждаете?")
    return "\n".join(parts)


def execute_confirm(
    *,
    client: Any,
    payload: dict[str, Any],
    tenant: Any,
    bot_user: Any,
) -> BookingToolResult:
    """Run the actual YClients create + persist BookingRequest.

    Called from :mod:`apps.bookings.callbacks` on a ✅ tap that
    successfully consumed the :class:`PendingBookingAction` row.

    Idempotency: existing :class:`BookingRequest` rows are looked up
    by a ``yclients_record_id=<id>`` marker in the ``comment`` field
    (mirrors the admin-webhook scheme). Belt-and-braces — the
    pending-action CAS is the first line; this is the safety net for
    the impossible-but-not-truly-impossible double-execute scenario.
    """
    master_id = _coerce_id(payload.get("master_id"))
    service_id = _coerce_id(payload.get("service_id"))
    slot_datetime = str(payload.get("slot_datetime") or "").strip()
    phone = str(payload.get("client_phone") or "").strip()
    client_name = str(payload.get("client_name") or "Client")
    master_name = str(payload.get("master_name") or "")
    service_name = str(payload.get("service_name") or "")

    tenant_id = str(getattr(tenant, "id", ""))

    if master_id is None or service_id is None or not slot_datetime:
        # Payload integrity: the preview-time validator already
        # guaranteed these, so a missing field here means corruption
        # of the JSON blob between write and read. Surface loudly.
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="invalid_payload"),
            error="invalid_payload",
        )

    # Phase 4a-IMPL1 closure: validate visit_at BEFORE the YClients call
    # so a parse failure can't leave a YClients record without our
    # corresponding BookingRequest. The model validator at
    # apps/booking/models.py::_validate_attribution_metadata raises when
    # booking_source != 'external' AND visit_at IS NULL (Q-ATT-IMPL3);
    # we're about to flip booking_source to 'ai_direct', so guard up-front.
    visit_at_dt = _parse_iso_datetime(slot_datetime)
    if visit_at_dt is None:
        logger.warning(
            "booking.confirm.exec.unparseable_slot slot=%s",
            slot_datetime,
        )
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_confirm",
            outcome="invalid_slot_datetime",
        )
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="invalid_payload"),
            error="invalid_payload",
        )

    try:
        record: BookingRecord = client.create_record(
            staff_id=master_id,
            services=[service_id],
            datetime=slot_datetime,
            client_phone=phone,
            client_name=client_name,
            # AMD-002 (D6): выбор оплаты из диалогового намерения; default
            # true — обратная совместимость (старые payload'ы без поля
            # трактуются как «с предоплатой», поведение как раньше).
            payment_required=bool(payload.get("payment_required", True)),
        )
    except YClientsUnavailableError as exc:
        logger.warning("booking.confirm.exec.unavailable err=%s", exc)
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_confirm",
            outcome="yclients_unavailable",
        )
        write_audit(
            EVENT_BOOKING_CONFIRM_FAILED,
            target="BookingSkill",
            payload={"tenant_id": tenant_id, "reason": "yclients_unavailable"},
        )
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="yclients_unavailable"),
            error="yclients_unavailable",
        )
    except YClientsSpecialistUnavailableError as exc:
        # C1 (PILOT_CONTRACTS §2): Ayla rejected the NEW booking with 409
        # SUBSCRIPTION_PAST_DUE. The customer sees a NEUTRAL slug — the
        # debt reason must never leak to the client surface; the concierge
        # offers another master/time instead. Internal code stays in logs
        # + audit (allowed by C1 — internal/backend may hold the reason).
        logger.info("booking.confirm.exec.specialist_unavailable err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="execute_confirm", outcome="specialist_unavailable")
        write_audit(
            EVENT_BOOKING_CONFIRM_FAILED,
            target="BookingSkill",
            payload={"tenant_id": tenant_id, "reason": "specialist_unavailable"},
        )
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="specialist_unavailable"),
            error="specialist_unavailable",
        )
    except YClientsAPIError as exc:
        logger.info("booking.confirm.exec.api_error err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="execute_confirm", outcome="yclients_api_error")
        write_audit(
            EVENT_BOOKING_CONFIRM_FAILED,
            target="BookingSkill",
            payload={"tenant_id": tenant_id, "reason": "yclients_api_error"},
        )
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="yclients_api_error"),
            error="yclients_api_error",
        )

    # Flag OFF: yc_id is the YClients int record id. Flag ON: the canonical
    # Ayla appointment UUID (record.record_id is 0 on that path). The marker
    # key name is kept across both providers so the reader convention is one.
    yc_id = str(_confirm_record_handle(record))
    if _booking_via_ayla() and not yc_id:
        # Create reported success but carried no canonical id — malformed.
        # Treat as an API error so the caller hands off rather than writing a
        # marker-less ("yclients_record_id=") row we could never resolve.
        logger.warning("booking.confirm.exec.ayla_no_appointment_id")
        _audit_tool(tenant_id=tenant_id, tool="execute_confirm", outcome="ayla_no_appointment_id")
        return BookingToolResult(
            confirmation=ConfirmationResult(ok=False, error="yclients_api_error"),
            error="yclients_api_error",
        )
    yc_marker = f"yclients_record_id={yc_id}"
    # Skills retro hotfix #1: anchor the idempotency lookup to the
    # documented comment prefix instead of substring-matching a
    # free-text field. A spoofed service_name / client_name carrying
    # ``yclients_record_id=<other-id>`` would otherwise short-circuit
    # a legitimate confirm. Both writers use this exact prefix:
    #   - execute_confirm:    "Bot booking | yclients_record_id=<id>"
    #   - execute_reschedule: "Bot booking | yclients_record_id=<id> | rescheduled_from=..."
    _idem_prefix = f"Bot booking | {yc_marker}"

    existing = BookingRequest.all_tenants.filter(
        tenant=tenant,
        bot_user=bot_user,
        comment__startswith=_idem_prefix,
    ).first()

    # visit_at_dt was parsed above (pre-YClients), reuse here.
    visit_at_iso = visit_at_dt.isoformat()

    # The `existing` lookup is the idempotency short-circuit for tool
    # replay (LLM retry, double-tap). Rows written before this PR have
    # booking_source='external' and we deliberately do NOT update them
    # here — backfilling legacy rows is a separate migration question.
    if existing is None:
        # Phase 4a-IMPL1 closure: bot-confirm path = ai_direct attribution.
        # Without this, BookingRequest.booking_source defaults to
        # "external" and the row is NOT billable — bot bookings would
        # silently fall out of the billing pipeline (per attribution
        # policy §6 «billable = ai_direct AND CONFIRMED»).
        billable, billing_reason = compute_billable(
            booking_source="ai_direct",
            status=BookingRequest.Status.CONFIRMED,
        )
        # Skills retro hotfix #2: mirror Hotfix D's split-brain recovery
        # for the confirm path. YClients create_record already succeeded
        # (record exists on their side). If the local BookingRequest
        # write raises — DB transient, future model-validator addition,
        # JSONField surprise — we MUST compensate by cancelling the
        # YClients record. Otherwise the customer holds a real
        # appointment that our DB / reminders / Loyalty cannot see.
        try:
            BookingRequest.all_tenants.create(
                tenant=tenant,
                bot_user=bot_user,
                category_name="",
                service_name=service_name or "—",
                master_name=master_name or "—",
                client_name=client_name,
                client_phone=phone,
                comment=_idem_prefix,
                source="bot",
                is_processed=False,
                status=BookingRequest.Status.CONFIRMED,
                visit_at=visit_at_dt,
                booking_source="ai_direct",
                ai_assist_score=compute_assist_score(booking_source="ai_direct"),
                billable=billable,
                billing_reason=billing_reason,
                attribution_metadata=build_customer_attribution_metadata(
                    booking_created_at=dj_timezone.now().isoformat(),
                    test_mode=False,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — recover, do not propagate
            logger.exception(
                "booking.confirm.local_create_failed yc_id=%s err=%s",
                yc_id,
                exc,
            )
            compensate_ok = False
            try:
                # Flag OFF: YClients wants int (byte-for-byte). Flag ON: yc_id
                # is the Ayla appointment UUID — pass through unchanged.
                client.cancel_record(record_id=yc_id if _booking_via_ayla() else int(yc_id))
                compensate_ok = True
            except Exception:  # noqa: BLE001 — compensate is best-effort
                logger.exception(
                    "booking.confirm.compensate_cancel_failed yc_id=%s",
                    yc_id,
                )

            _audit_tool(
                tenant_id=tenant_id,
                tool="execute_confirm",
                outcome="local_create_failed_compensated"
                if compensate_ok
                else "local_create_failed_split_brain",
            )
            write_audit(
                EVENT_BOOKING_CONFIRM_FAILED,
                target="BookingSkill",
                payload={
                    "tenant_id": tenant_id,
                    "yclients_record_id": yc_id,
                    "reason": "local_create_failed",
                    "compensate_ok": compensate_ok,
                },
            )
            # Don't lie to the caller: if compensate failed, split-brain
            # persists. Either way the confirm did not complete from the
            # customer's perspective on our side, so report failure and
            # let the caller hand off to a human operator.
            return BookingToolResult(
                confirmation=ConfirmationResult(
                    ok=False,
                    error="booking_confirm_partial_failure",
                ),
                error="booking_confirm_partial_failure",
            )
        # Flag ON: mirror the canonical Ayla appointment locally (ADR-0009).
        # No-op under flag OFF — the YClients path has no canonical mirror.
        _upsert_remote_booking_proxy(
            tenant=tenant,
            bot_user=bot_user,
            record=record,
            start_at=visit_at_dt,
        )
        _schedule_reminders(
            tenant=tenant,
            bot_user=bot_user,
            yc_id=yc_id,
            visit_at_dt=visit_at_dt,
            master_name=master_name,
            service_name=service_name,
        )

    _audit_tool(tenant_id=tenant_id, tool="execute_confirm", outcome="ok")
    write_audit(
        EVENT_BOOKING_CONFIRMED,
        target="BookingSkill",
        payload={
            "tenant_id": tenant_id,
            "yclients_record_id": yc_id,
            "idempotent": existing is not None,
        },
    )
    confirmation = ConfirmationResult(
        ok=True,
        # Flag ON: surface the canonical Ayla UUID; flag OFF: the int record id.
        record_id=yc_id if _booking_via_ayla() else record.record_id,
        visit_at=visit_at_iso,
        master_name=master_name,
        service_name=service_name,
    )
    text = _format_confirmation_text(confirmation)
    return BookingToolResult(text=text, confirmation=confirmation)


def _schedule_reminders(
    *,
    tenant: Any,
    bot_user: Any,
    yc_id: str,
    visit_at_dt: datetime | None,
    master_name: str,
    service_name: str,
) -> None:
    """Best-effort reminder pair (T-24h + T-2h) via the R1 factory.

    Delegates to :func:`apps.bookings.reminders_factory.create_reminders_for_booking`
    — the single source of truth for reminder scheduling. Updated in
    R1 (DRF-844) from the original single-DAY_BEFORE write so both
    reminders get scheduled at confirm time; this matches the B2
    admin-webhook path which has always written both.

    Best-effort: failing to write reminders MUST NOT fail the
    booking (the YClients record is already committed). The factory
    itself swallows ``chat_id`` absences; broader exceptions are
    caught here so an ORM hiccup doesn't break the confirm reply.
    """
    if visit_at_dt is None:
        return
    try:
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id=yc_id,
            visit_at=visit_at_dt,
            master_name=master_name,
            service_name=service_name,
        )
    except Exception:  # noqa: BLE001 — reminder is best-effort
        logger.exception("booking.reminder.schedule_failed yc_id=%s", yc_id)


def _format_confirmation_text(confirmation: ConfirmationResult) -> str:
    if not confirmation.ok:
        return "Не удалось создать запись — переключу на менеджера."
    parts: list[str] = ["Готово! Записала."]
    if confirmation.master_name:
        parts.append(f"Мастер: {confirmation.master_name}.")
    if confirmation.service_name:
        parts.append(f"Услуга: {confirmation.service_name}.")
    if confirmation.visit_at:
        parts.append(f"Время: {confirmation.visit_at}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Tool 4: cancel_booking (preview) + execute_cancel
# ---------------------------------------------------------------------------


def cancel_booking(
    *,
    client: Any,
    arguments: dict[str, Any],
    tenant: Any,
    bot_user: Any,
) -> BookingToolResult:
    """Preview a customer-initiated cancellation.

    Args:
      client: B1 YClients client. UNUSED on the preview path — kept
        for skill-dispatch symmetry.
      arguments: LLM-supplied ``{record_id, reason?}``.
      tenant: current :class:`Tenant`.
      bot_user: current :class:`BotUser`.

    Anti-hallucination: ``record_id`` is validated against the user's
    own :class:`BookingRequest` rows (matched via the
    ``yclients_record_id=<id>`` comment marker). A bogus id maps to
    ``error="invalid_record_id"``.

    Returns a :class:`BookingToolResult` with ``pending`` populated.
    """
    record_id = _coerce_id(arguments.get("record_id"))
    reason = str(arguments.get("reason") or "").strip()
    tenant_id = str(getattr(tenant, "id", ""))
    _ = client

    if record_id is None:
        _audit_tool(tenant_id=tenant_id, tool="cancel_booking", outcome="missing_record_id")
        return BookingToolResult(error="invalid_record_id")

    booking = _find_user_booking(tenant=tenant, bot_user=bot_user, record_id=record_id)
    if booking is None:
        _audit_tool(tenant_id=tenant_id, tool="cancel_booking", outcome="invalid_record_id")
        return BookingToolResult(error="invalid_record_id")

    payload = {
        "record_id": record_id,
        "reason": reason,
        "booking_request_id": str(booking.id),
        "master_name": booking.master_name,
        "service_name": booking.service_name,
    }
    token = create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.CANCEL,
        payload=payload,
    )

    preview_text = _format_cancel_preview(booking=booking, reason=reason)
    keyboard = confirm_2_button(str(token))

    _audit_tool(tenant_id=tenant_id, tool="cancel_booking", outcome="preview")
    write_audit(
        EVENT_BOOKING_PREVIEW,
        target="BookingSkill",
        payload={
            "tenant_id": tenant_id,
            "kind": "cancel",
            "record_id": record_id,
            "token": str(token),
        },
    )
    return BookingToolResult(
        text=preview_text,
        pending=PendingPreview(
            kind=PendingBookingAction.Kind.CANCEL,
            token=token,
            preview_text=preview_text,
            keyboard=keyboard,
        ),
    )


def _format_cancel_preview(*, booking: BookingRequest, reason: str) -> str:
    parts = ["Отменяю запись:"]
    if booking.service_name:
        parts.append(f"• Услуга: {booking.service_name}")
    if booking.master_name:
        parts.append(f"• Мастер: {booking.master_name}")
    if reason:
        parts.append(f"• Причина: {reason}")
    parts.append("Подтверждаете отмену?")
    return "\n".join(parts)


def execute_cancel(
    *,
    client: Any,
    payload: dict[str, Any],
    tenant: Any,
    bot_user: Any,
) -> BookingToolResult:
    """Actually cancel the YClients record + flip BookingRequest status.

    Called from :mod:`apps.bookings.callbacks` after the
    :class:`PendingBookingAction` CAS succeeds on a ✅ tap.

    Side-effects on success:

    * YClients ``cancel_record(record_id=...)``.
    * ``BookingRequest.status`` flipped to ``CANCELLED``.
    * Reason text appended to ``BookingRequest.comment`` (append, not
      overwrite — preserves the original ``yclients_record_id=<id>``
      marker and any prior history).
    * Any pending ``BookingReminder`` rows for this ``yclients_record_id``
      → status ``CANCELLED`` (so the reminder beat doesn't dispatch
      a reminder for a cancelled booking).

    On YClients failure: returns ``error="yclients_cancel_failure"``
    WITHOUT flipping status. Caller (callback handler) maps this to a
    handoff so an operator can manually cancel in the YClients UI.
    """
    record_id = _coerce_id(payload.get("record_id"))
    reason = str(payload.get("reason") or "").strip()
    tenant_id = str(getattr(tenant, "id", ""))

    if record_id is None:
        return BookingToolResult(error="invalid_payload")

    # Re-validate ownership at execute time — defence-in-depth against
    # a leaked token being executed for a stranger's booking.
    booking = _find_user_booking(tenant=tenant, bot_user=bot_user, record_id=record_id)
    if booking is None:
        _audit_tool(tenant_id=tenant_id, tool="execute_cancel", outcome="invalid_record_id")
        return BookingToolResult(error="invalid_record_id")

    try:
        client.cancel_record(record_id=record_id)
    except YClientsUnavailableError as exc:
        logger.warning("booking.cancel.exec.unavailable err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="execute_cancel", outcome="yclients_unavailable")
        write_audit(
            EVENT_BOOKING_CANCEL_FAILED,
            target="BookingSkill",
            payload={
                "tenant_id": tenant_id,
                "record_id": record_id,
                "reason": "yclients_unavailable",
            },
        )
        return BookingToolResult(error="yclients_cancel_failure")
    except YClientsAPIError as exc:
        logger.info("booking.cancel.exec.api_error err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="execute_cancel", outcome="yclients_api_error")
        write_audit(
            EVENT_BOOKING_CANCEL_FAILED,
            target="BookingSkill",
            payload={
                "tenant_id": tenant_id,
                "record_id": record_id,
                "reason": "yclients_api_error",
            },
        )
        return BookingToolResult(error="yclients_cancel_failure")

    new_comment = booking.comment or ""
    if reason:
        suffix = f" | cancelled: {reason}"
        if suffix not in new_comment:
            new_comment = new_comment + suffix

    BookingRequest.all_tenants.filter(pk=booking.pk).update(
        status=BookingRequest.Status.CANCELLED,
        comment=new_comment,
    )

    # Flag ON: flip the canonical mirror to CANCELLED too (ADR-0009). No-op
    # under flag OFF (no mirror on the YClients path).
    _mirror_cancel(tenant=tenant, record_id=record_id)

    # Cancel any still-pending reminders so the beat doesn't dispatch
    # for a row the user already cancelled. We only flip PENDING rows;
    # SENT_NO_REPLY / SENT / CONFIRMED / etc. are left alone (those
    # represent a real history of communication that audit should
    # keep). Import lazily to avoid the top-level cycle.
    from apps.booking.models import BookingReminder

    BookingReminder.all_tenants.filter(
        yclients_record_id=str(record_id),
        status=BookingReminder.Status.PENDING,
    ).update(status=BookingReminder.Status.CANCELLED)

    _audit_tool(tenant_id=tenant_id, tool="execute_cancel", outcome="ok")
    write_audit(
        EVENT_BOOKING_CANCELLED,
        target="BookingSkill",
        payload={"tenant_id": tenant_id, "record_id": record_id},
    )

    # Domain bus — taxonomy §3.1 booking.cancelled. Swallow errors so a
    # bus outage never unwinds the cancel side-effect.
    try:
        eventbus_services.emit_booking_cancelled(
            booking_id=str(booking.pk),
            cancelled_by="customer",
            cancellation_reason=reason or "",
            cancelled_at=dj_timezone.now().isoformat(),
            actor_type="human",
            actor_role="customer",
            actor_id=str(bot_user.pk) if bot_user else None,
            tenant=tenant,
        )
    except Exception:  # noqa: BLE001 — domain telemetry never breaks the tool
        logger.exception("booking.cancel.eventbus_emit_failed booking=%s", booking.pk)

    text = "Готово, запись отменена."
    return BookingToolResult(text=text)


# ---------------------------------------------------------------------------
# Tool 5: reschedule_booking (preview) + execute_reschedule
# ---------------------------------------------------------------------------


def reschedule_booking(
    *,
    client: Any,
    arguments: dict[str, Any],
    tenant: Any,
    bot_user: Any,
) -> BookingToolResult:
    """Preview a customer-initiated reschedule.

    Args:
      client: B1 YClients client — USED here (unlike the other preview
        tools) to validate the new slot is actually free before
        prompting the user.
      arguments: LLM-supplied ``{record_id, new_datetime}``.
      tenant: current :class:`Tenant`.
      bot_user: current :class:`BotUser`.

    Validation order:

    1. ``record_id`` must belong to ``bot_user``.
    2. ``new_datetime`` must parse as ISO and be in the future.
    3. ``new_datetime`` must be a slot the master has free per
       :meth:`YClientsAPI.get_available_times`. If not, returns
       ``error="slot_unavailable"`` so the skill emits a clarification
       reply.

    Returns ``pending``-only :class:`BookingToolResult` on success.
    """
    record_id = _coerce_id(arguments.get("record_id"))
    new_datetime = str(arguments.get("new_datetime") or "").strip()
    tenant_id = str(getattr(tenant, "id", ""))

    if record_id is None:
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="missing_record_id")
        return BookingToolResult(error="invalid_record_id")

    booking = _find_user_booking(tenant=tenant, bot_user=bot_user, record_id=record_id)
    if booking is None:
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="invalid_record_id")
        return BookingToolResult(error="invalid_record_id")

    new_dt = _parse_iso_datetime(new_datetime)
    if new_dt is None:
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="bad_datetime")
        return BookingToolResult(error="invalid_datetime")
    now = dj_timezone.now()
    if new_dt <= now:
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="past_datetime")
        return BookingToolResult(error="past_datetime")

    # Pull the original master/service so the reschedule keeps the same pair.
    # Flag OFF: YClients ``get_user_records`` (cancel-and-recreate path). Flag
    # ON: the canonical mirror (``RemoteBookingProxy.specialist_id/service_id``)
    # — the YClients records endpoint isn't the source of truth under Ayla.
    staff_id: int | str | None
    service_id: int | str | None
    if _booking_via_ayla():
        staff_id, service_id = _proxy_master_service(tenant=tenant, record_id=record_id)
    else:
        user_record = _find_yclients_user_record(client=client, record_id=record_id)
        if user_record is None:
            _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="record_not_found")
            return BookingToolResult(error="record_not_found")
        staff_id = _coerce_id((user_record.staff or {}).get("id"))
        services_list = user_record.services or []
        service_id = _coerce_id(services_list[0].get("id")) if services_list else None
    if staff_id is None or service_id is None:
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="malformed_record")
        return BookingToolResult(error="record_not_found")

    # Validate the new slot is actually free.
    target_date = new_dt.date().isoformat()
    target_time = new_dt.strftime("%H:%M")
    try:
        times = client.get_available_times(
            staff_id=staff_id,
            date=target_date,
            service_ids=[service_id],
        )
    except YClientsUnavailableError:
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="yclients_unavailable")
        return BookingToolResult(error="yclients_unavailable")
    except YClientsAPIError:
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="yclients_api_error")
        return BookingToolResult(error="yclients_api_error")

    if not _slot_available(times, target_time, new_datetime):
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="slot_unavailable")
        return BookingToolResult(error="slot_unavailable")

    payload = {
        "record_id": record_id,
        "new_datetime": new_datetime,
        "master_id": staff_id,
        "service_id": service_id,
        "master_name": booking.master_name,
        "service_name": booking.service_name,
        "client_phone": booking.client_phone,
        "client_name": booking.client_name,
        "booking_request_id": str(booking.id),
    }
    token = create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.RESCHEDULE,
        payload=payload,
    )

    preview_text = _format_reschedule_preview(
        booking=booking,
        new_datetime=new_datetime,
    )
    keyboard = confirm_2_button(str(token))

    _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="preview")
    write_audit(
        EVENT_BOOKING_PREVIEW,
        target="BookingSkill",
        payload={
            "tenant_id": tenant_id,
            "kind": "reschedule",
            "record_id": record_id,
            "token": str(token),
        },
    )
    return BookingToolResult(
        text=preview_text,
        pending=PendingPreview(
            kind=PendingBookingAction.Kind.RESCHEDULE,
            token=token,
            preview_text=preview_text,
            keyboard=keyboard,
        ),
    )


def _slot_available(
    times: list[AvailableTime],
    target_time: str,
    target_iso: str,
) -> bool:
    """True iff one of ``times`` matches ``target_time`` or ``target_iso``.

    Matches on either the ``HH:MM`` time field or the full ISO datetime
    — YClients returns one or the other depending on company config.
    """
    for t in times:
        if t.datetime and t.datetime == target_iso:
            return True
        if t.time and t.time == target_time:
            return True
    return False


def _format_reschedule_preview(
    *,
    booking: BookingRequest,
    new_datetime: str,
) -> str:
    parts = ["Переношу запись:"]
    if booking.service_name:
        parts.append(f"• Услуга: {booking.service_name}")
    if booking.master_name:
        parts.append(f"• Мастер: {booking.master_name}")
    parts.append(f"• Новое время: {new_datetime}")
    parts.append("Подтверждаете перенос?")
    return "\n".join(parts)


def _execute_reschedule_ayla(
    *,
    client: Any,
    tenant: Any,
    bot_user: Any,
    tenant_id: str,
    booking: BookingRequest,
    record_id: int | str,
    master_id: int | str | None,
    service_id: int | str | None,
    new_datetime: str,
    visit_at_dt: datetime,
    master_name: str,
    service_name: str,
) -> BookingToolResult:
    """Native Ayla reschedule (flag ON) — preserves the canonical appointment
    id *and* duration, so there is no cancel-and-recreate.

    Consequences vs the flag-OFF path: the BookingRequest billing row and its
    ``yclients_record_id=<uuid>`` marker stay put (the id didn't change, so the
    attribution chain is intact); only ``visit_at`` + the mirror move. Mirrors
    the slot-recheck UX (a taken slot → ``slot_unavailable`` clarification)
    before touching the canonical booking.
    """
    # Slot recheck (parity with the flag-OFF execute) — surfaces a taken slot
    # as a clarification rather than letting the native call hard-fail.
    if master_id is not None and service_id is not None:
        try:
            avail = client.get_available_times(
                staff_id=master_id,
                date=visit_at_dt.date().isoformat(),
                service_ids=[service_id],
            )
        except YClientsUnavailableError:
            _audit_tool(
                tenant_id=tenant_id, tool="execute_reschedule", outcome="recheck_unavailable"
            )
            return BookingToolResult(error="yclients_unavailable")
        except YClientsAPIError:
            _audit_tool(tenant_id=tenant_id, tool="execute_reschedule", outcome="recheck_api_error")
            return BookingToolResult(error="yclients_api_error")
        if not _slot_available(avail, visit_at_dt.strftime("%H:%M"), new_datetime):
            _audit_tool(
                tenant_id=tenant_id, tool="execute_reschedule", outcome="slot_taken_at_execute"
            )
            return BookingToolResult(error="slot_unavailable")

    try:
        record: BookingRecord = client.reschedule_record(
            record_id=record_id,
            datetime=new_datetime,
        )
    except YClientsUnavailableError as exc:
        logger.warning("booking.reschedule.ayla.unavailable err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="execute_reschedule", outcome="ayla_unavailable")
        return BookingToolResult(error="yclients_cancel_failure")
    except YClientsAPIError as exc:
        logger.info("booking.reschedule.ayla.api_error err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="execute_reschedule", outcome="ayla_api_error")
        return BookingToolResult(error="yclients_api_error")

    # Move the billing row's visit_at in place — status stays CONFIRMED, marker
    # + attribution chain untouched (the canonical id is unchanged).
    BookingRequest.all_tenants.filter(pk=booking.pk).update(visit_at=visit_at_dt)

    # Refresh the mirror to the new window (Ayla preserves duration server-side;
    # the native response carries the new end, so the upsert reflects it).
    _upsert_remote_booking_proxy(
        tenant=tenant,
        bot_user=bot_user,
        record=record,
        start_at=visit_at_dt,
    )

    # Re-point reminders at the new time, same canonical id (best-effort).
    from apps.booking.models import BookingReminder

    BookingReminder.all_tenants.filter(
        yclients_record_id=str(record_id),
        status=BookingReminder.Status.PENDING,
    ).update(status=BookingReminder.Status.CANCELLED)
    _schedule_reminders(
        tenant=tenant,
        bot_user=bot_user,
        yc_id=str(record_id),
        visit_at_dt=visit_at_dt,
        master_name=master_name or booking.master_name,
        service_name=service_name or booking.service_name,
    )

    # Domain bus — booking.rescheduled.
    try:
        _old_visit_iso = booking.visit_at.isoformat() if booking.visit_at else ""
        eventbus_services.emit_booking_rescheduled(
            booking_id=str(booking.pk),
            old_slot_start=_old_visit_iso,
            new_slot_start=new_datetime,
            rescheduled_by="customer",
            actor_type="human",
            actor_role="customer",
            actor_id=str(bot_user.pk) if bot_user else None,
            correlation_id=eventbus_services.new_correlation_id(),
            tenant=tenant,
        )
    except Exception:  # noqa: BLE001 — domain telemetry never breaks the tool
        logger.exception("booking.reschedule.ayla.eventbus_emit_failed booking=%s", booking.pk)

    _audit_tool(tenant_id=tenant_id, tool="execute_reschedule", outcome="ok")
    confirmation = ConfirmationResult(
        ok=True,
        record_id=str(record_id),
        visit_at=visit_at_dt.isoformat(),
        master_name=master_name or booking.master_name,
        service_name=service_name or booking.service_name,
    )
    return BookingToolResult(
        text=_format_confirmation_text(confirmation), confirmation=confirmation
    )


def execute_reschedule(
    *,
    client: Any,
    payload: dict[str, Any],
    tenant: Any,
    bot_user: Any,
) -> BookingToolResult:
    """Run the actual cancel-and-create reschedule.

    Returns a :class:`BookingToolResult` carrying the new
    :class:`ConfirmationResult` on success, or one of these errors:

    * ``"yclients_cancel_failure"`` — could not cancel the old record.
      Old row stays as-is; no new record created.
    * ``"booking_reschedule_partial_failure"`` — cancel succeeded but
      create failed. This is the destructive corner case: the old
      ``BookingRequest.status`` IS flipped to ``cancelled`` (so the
      data layer reflects the YClients reality) and the caller emits
      a manager notification so an operator can rebook manually. We
      DO NOT retry the create — risk of double-booking is worse than
      the inconvenience of one handoff.
    """
    record_id = _coerce_id(payload.get("record_id"))
    new_datetime = str(payload.get("new_datetime") or "").strip()
    master_id = _coerce_id(payload.get("master_id"))
    service_id = _coerce_id(payload.get("service_id"))
    master_name = str(payload.get("master_name") or "")
    service_name = str(payload.get("service_name") or "")
    phone = str(payload.get("client_phone") or "")
    client_name = str(payload.get("client_name") or "Client")
    tenant_id = str(getattr(tenant, "id", ""))

    if record_id is None or master_id is None or service_id is None or not new_datetime:
        return BookingToolResult(error="invalid_payload")

    # Phase 4a-IMPL1 closure: validate new_datetime BEFORE the YClients
    # cancel call so a parse failure can't leave us in a half-state
    # (old record cancelled but new BookingRequest write fails on the
    # ai_direct + visit_at=NULL validator). Mirrors execute_confirm.
    visit_at_dt = _parse_iso_datetime(new_datetime)
    if visit_at_dt is None:
        logger.warning(
            "booking.reschedule.exec.unparseable_slot slot=%s",
            new_datetime,
        )
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_reschedule",
            outcome="invalid_new_datetime",
        )
        return BookingToolResult(error="invalid_payload")

    booking = _find_user_booking(tenant=tenant, bot_user=bot_user, record_id=record_id)
    if booking is None:
        _audit_tool(tenant_id=tenant_id, tool="execute_reschedule", outcome="invalid_record_id")
        return BookingToolResult(error="invalid_record_id")

    # Tech-lead double-pass S1: enforce CONFIRMED-only at the LLM tool
    # layer, mirroring ``apps.booking.services.reschedule.py:74``.
    # Without this, a customer who tapped Cancel (status →
    # CANCEL_REQUESTED during the 5s undo window) then tapped Reschedule
    # via the LLM would have the LLM tool happily reschedule a cancel-
    # pending row. Service-layer rejects; LLM path used to silently
    # accept. Same business invariant now enforced in BOTH places.
    # Q12-α #560 (PRE_PILOT 2026-07-15): ALLOW-list — mirrors
    # services/reschedule.py gate. Default-deny for any future enum
    # addition.
    if booking.status not in get_reschedulable_statuses():
        logger.info(
            "booking.reschedule.exec.not_reschedulable record_id=%s status=%s",
            record_id,
            booking.status,
        )
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_reschedule",
            outcome="not_reschedulable_status",
        )
        return BookingToolResult(error="not_reschedulable")

    # Flag ON: Ayla supports a NATIVE reschedule that preserves the canonical
    # appointment id (and duration), so we don't cancel-and-recreate. This
    # branch returns before the YClients cancel+create chain below, which
    # stays byte-for-byte the flag-OFF path.
    if _booking_via_ayla():
        return _execute_reschedule_ayla(
            client=client,
            tenant=tenant,
            bot_user=bot_user,
            tenant_id=tenant_id,
            booking=booking,
            record_id=record_id,
            master_id=master_id,
            service_id=service_id,
            new_datetime=new_datetime,
            visit_at_dt=visit_at_dt,
            master_name=master_name,
            service_name=service_name,
        )

    # Skills retro hotfix #4: re-check slot availability at execute time.
    # The preview-time check (reschedule_booking) is up to 10 min stale
    # (pending-action expiry window). Between preview ✓ and ✅ tap,
    # another customer can claim the slot. If we proceed to cancel the
    # old record first and the new slot is already taken, YClients
    # rejects create_record → user loses their old booking AND has no
    # new one. Re-checking here shrinks the race window from minutes to
    # milliseconds (still not zero — another concurrent execute could
    # squeak in — but the broker semantics of YClients' create_record
    # close that residual window on their side).
    try:
        _avail_times = client.get_available_times(
            staff_id=master_id,
            date=visit_at_dt.date().isoformat(),
            service_ids=[service_id],
        )
    except YClientsUnavailableError as exc:
        logger.warning("booking.reschedule.recheck.unavailable err=%s", exc)
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_reschedule",
            outcome="recheck_unavailable",
        )
        return BookingToolResult(error="yclients_unavailable")
    except YClientsAPIError as exc:
        logger.info("booking.reschedule.recheck.api_error err=%s", exc)
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_reschedule",
            outcome="recheck_api_error",
        )
        return BookingToolResult(error="yclients_api_error")
    if not _slot_available(
        _avail_times,
        visit_at_dt.strftime("%H:%M"),
        new_datetime,
    ):
        # Old record stays intact — no destructive action yet. Surface a
        # clear «slot taken» error so the caller can ask for another
        # time without losing the current booking.
        logger.info(
            "booking.reschedule.recheck.slot_taken record_id=%s new_datetime=%s",
            record_id,
            new_datetime,
        )
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_reschedule",
            outcome="slot_taken_at_execute",
        )
        return BookingToolResult(error="slot_unavailable")

    # Step 1: cancel the old record.
    try:
        client.cancel_record(record_id=record_id)
    except YClientsUnavailableError as exc:
        logger.warning("booking.reschedule.cancel.unavailable err=%s", exc)
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_reschedule",
            outcome="cancel_unavailable",
        )
        return BookingToolResult(error="yclients_cancel_failure")
    except YClientsAPIError as exc:
        logger.info("booking.reschedule.cancel.api_error err=%s", exc)
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_reschedule",
            outcome="cancel_api_error",
        )
        return BookingToolResult(error="yclients_cancel_failure")

    # Cancel of old record succeeded. Mark the old BookingRequest
    # ``RESCHEDULED`` (terminal — replaced) and cancel pending
    # reminders for it.
    from apps.booking.models import BookingReminder

    BookingRequest.all_tenants.filter(pk=booking.pk).update(
        status=BookingRequest.Status.RESCHEDULED,
    )
    BookingReminder.all_tenants.filter(
        yclients_record_id=str(record_id),
        status=BookingReminder.Status.PENDING,
    ).update(status=BookingReminder.Status.CANCELLED)

    # Domain bus — taxonomy §3.1 booking.rescheduled. Fresh correlation_id
    # captured so the partial-failure cancel below (if it happens) can
    # share it — subscribers then recognise the composite "old visit
    # dead, no new visit booked" outcome.
    _reschedule_correlation_id = eventbus_services.new_correlation_id()
    try:
        _old_visit_iso = booking.visit_at.isoformat() if booking.visit_at else ""
        eventbus_services.emit_booking_rescheduled(
            booking_id=str(booking.pk),
            old_slot_start=_old_visit_iso,
            new_slot_start=new_datetime,
            rescheduled_by="customer",
            actor_type="human",
            actor_role="customer",
            actor_id=str(bot_user.pk) if bot_user else None,
            correlation_id=_reschedule_correlation_id,
            tenant=tenant,
        )
    except Exception:  # noqa: BLE001 — domain telemetry never breaks the tool
        logger.exception("booking.reschedule.eventbus_emit_failed booking=%s", booking.pk)

    # Step 2: create the new record. Partial-failure handling per
    # the module docstring.
    try:
        record: BookingRecord = client.create_record(
            staff_id=master_id,
            services=[service_id],
            datetime=new_datetime,
            client_phone=phone,
            client_name=client_name,
        )
    except (YClientsUnavailableError, YClientsAPIError) as exc:
        # Partial failure — old cancelled, new failed. Flip the old
        # row to CANCELLED (not RESCHEDULED, because there's no new
        # row replacing it). The caller emits the handoff +
        # manager-chat notification.
        logger.error(
            "booking.reschedule.partial_failure record_id=%s err=%s",
            record_id,
            exc,
        )
        BookingRequest.all_tenants.filter(pk=booking.pk).update(
            status=BookingRequest.Status.CANCELLED,
        )
        # Reschedule degraded to cancel — emit booking.cancelled with the
        # SAME correlation_id as the booking.rescheduled above so subscribers
        # see the composite outcome.
        try:
            eventbus_services.emit_booking_cancelled(
                booking_id=str(booking.pk),
                cancelled_by="system",
                cancellation_reason="reschedule_partial_failure",
                cancelled_at=dj_timezone.now().isoformat(),
                actor_type="system",
                correlation_id=_reschedule_correlation_id,
                tenant=tenant,
            )
        except Exception:  # noqa: BLE001 — domain telemetry never breaks the tool
            logger.exception("booking.reschedule.partial_cancel_emit_failed booking=%s", booking.pk)
        _audit_tool(
            tenant_id=tenant_id,
            tool="execute_reschedule",
            outcome="partial_failure",
        )
        write_audit(
            EVENT_BOOKING_RESCHEDULE_PARTIAL,
            target="BookingSkill",
            payload={
                "tenant_id": tenant_id,
                "old_record_id": record_id,
                "new_datetime": new_datetime,
                "master_id": master_id,
                "service_id": service_id,
                # Q12-α (founder ACK 2026-05-22 decision #4): a
                # partial-failure reschedule is a chain TERMINATOR —
                # the old row is cancelled, the new row never written,
                # so any future booking the customer makes is a fresh
                # billable sale (a new chain via execute_confirm).
                # Surface this on the audit row so admin/ops dashboards
                # can filter for «manual rebook required» tickets
                # without having to cross-reference the event_type.
                "q12a_chain_terminator": True,
                "q12a_chain_terminator_reason": "partial_failure",
            },
        )
        return BookingToolResult(error="booking_reschedule_partial_failure")

    new_yc_id = str(record.record_id)
    new_yc_marker = f"yclients_record_id={new_yc_id}"
    # visit_at_dt was parsed at the top of this function (pre-cancel).
    visit_at_iso = visit_at_dt.isoformat()

    # Skills retro hotfix #1: anchor on the documented Bot-booking prefix
    # (see execute_confirm for rationale). The reschedule writer below
    # uses ``"Bot booking | yclients_record_id=<id> | rescheduled_from=..."``
    # — startswith on the first two tokens is sufficient and not spoofable
    # via free-text comment payload.
    _new_idem_prefix = f"Bot booking | {new_yc_marker}"
    existing_new = BookingRequest.all_tenants.filter(
        tenant=tenant,
        bot_user=bot_user,
        comment__startswith=_new_idem_prefix,
    ).first()
    if existing_new is None:
        # Tech-lead double-pass S3: wrap the chain compute + new-row
        # write in a single ``transaction.atomic()`` with
        # ``select_for_update`` on the OLD booking. Without this, two
        # concurrent reschedule calls on different chain links could
        # both walk to the same root, both pass continuation check, and
        # both write new continuation rows pointing at the root — a
        # chain fork. The atomic + lock mirrors
        # ``apps.booking.services.reschedule.py:63`` so both reschedule
        # paths now enforce the same concurrency contract.
        from django.db import transaction as _db_transaction

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        with _db_transaction.atomic():
            booking = (
                BookingRequest.all_tenants.select_for_update()
                .select_related("service")
                .get(pk=booking.pk)
            )

            # Q12-α continuation decision (issue #478, founder ACK
            # 2026-05-22). The LLM reschedule tool always keeps the
            # same service + master (the LLM prompt enforces it); a
            # service swap is structurally not a normal LLM flow today.
            # We pass ``booking.service_id`` as ``new_service_id`` so
            # the helper's strict-equality check is tautologically
            # satisfied — the 90-day chain-root threshold is the
            # meaningful gate at this layer. If the LLM ever starts
            # allowing service swaps from this path, this call site
            # MUST be updated.
            # Q12-α #541 (founder ACK 2026-05-23): live commercial
            # identity for chain-break detection. ``booking.service``
            # may be None on legacy LLM rows (B1 adversarial-pass note);
            # in that case skip the snapshot — the comparator's NULL-on-
            # either-side rule keeps the chain intact and preserves the
            # legacy chain (consistent with q12a-billing-founder-gate).
            # #618 follow-up: shape lives in
            # ``attribution.build_live_commercial_identity``.
            live_commercial_identity: CommercialIdentitySnapshot | None = (
                build_live_commercial_identity(booking.service)
                if booking.service is not None
                else None
            )

            is_continuation, chain_break_reason, chain_root_id = compute_reschedule_continuation(
                old=booking,
                new_service_id=str(booking.service_id) if booking.service_id else "",
                new_visit_at=visit_at_dt,
                new_commercial_identity=live_commercial_identity,
            )

            # Carry root's snapshot on continuation; fresh-snapshot on
            # chain break. Mirrors services/reschedule.py logic.
            # #616 (PRE_PILOT 2026-07-15) — root read locked via bare
            # ``select_for_update()`` (``of=`` omitted, see
            # services/reschedule.py for full rationale). FOOT-GUN:
            # do NOT add ``.select_related(...)`` here — would expand
            # FOR UPDATE scope to joined tables on Postgres.
            carry_snapshot: CommercialIdentitySnapshot | None = None
            if is_continuation and chain_root_id is not None:
                try:
                    root = BookingRequest.all_tenants.select_for_update().get(id=chain_root_id)
                    carry_snapshot = root.commercial_identity_snapshot
                except BookingRequest.DoesNotExist:
                    carry_snapshot = None
            elif live_commercial_identity is not None:
                # Chain broken → new chain root, snapshot the current
                # live view so future reschedules anchor against it.
                carry_snapshot = live_commercial_identity

            billable, billing_reason = compute_billable(
                booking_source="ai_direct",
                status=BookingRequest.Status.CONFIRMED,
                created_by="execute_reschedule",
                is_reschedule_continuation=is_continuation,
                chain_break_reason=chain_break_reason,
            )
            reschedule_metadata = build_customer_attribution_metadata(
                booking_created_at=dj_timezone.now().isoformat(),
                test_mode=False,
            )
            reschedule_metadata["created_by"] = "execute_reschedule"
            reschedule_metadata["rescheduled_from_record_id"] = record_id
            reschedule_metadata["q12a_is_continuation"] = is_continuation
            if chain_break_reason:
                reschedule_metadata["q12a_chain_break_reason"] = chain_break_reason
            # Founder ACK 2026-05-23 option (b): mirror the snapshot
            # into attribution_metadata for the LLM path so an analyst
            # can replay the commercial-identity decision from a single
            # JSON blob without joining back through the chain.
            if carry_snapshot is not None:
                reschedule_metadata["commercial_identity_snapshot"] = carry_snapshot

            # Hotfix D (retro review #2): the window between YClients-
            # create success and local-write success is the last split-
            # brain gap the Q-ATT-IMPL1 closure didn't seal. The early
            # _parse_iso_datetime guard rejected unparseable slots
            # before any YClients call, but the local create can still
            # fail on DB transients, JSONField validation surprises, or
            # any future model-level validator additions. If we land
            # here without recovery, YClients has the customer's new
            # appointment but our DB and reminders + Loyalty don't know
            # about it.
            try:
                BookingRequest.all_tenants.create(
                    tenant=tenant,
                    bot_user=bot_user,
                    category_name="",
                    service_name=service_name or booking.service_name or "—",
                    master_name=master_name or booking.master_name or "—",
                    client_name=client_name,
                    client_phone=phone,
                    comment=(f"Bot booking | {new_yc_marker} | rescheduled_from={record_id}"),
                    source="bot",
                    is_processed=False,
                    status=BookingRequest.Status.CONFIRMED,
                    visit_at=visit_at_dt,
                    booking_source="ai_direct",
                    ai_assist_score=compute_assist_score(booking_source="ai_direct"),
                    billable=billable,
                    billing_reason=billing_reason,
                    attribution_metadata=reschedule_metadata,
                    original_booking_event_id=chain_root_id,
                    commercial_identity_snapshot=carry_snapshot,
                )
            except Exception as exc:  # noqa: BLE001 — recover, do not propagate
                # Split-brain recovery: try to cancel the new YClients record
                # so the customer isn't double-booked on their side. Then
                # treat this as a reschedule_partial_failure for analytics.
                logger.exception(
                    "booking.reschedule.local_create_failed yc_id=%s err=%s",
                    new_yc_id,
                    exc,
                )
                compensate_ok = False
                try:
                    client.cancel_record(record_id=int(new_yc_id))
                    compensate_ok = True
                except Exception:  # noqa: BLE001 — compensate is best-effort
                    logger.exception(
                        "booking.reschedule.compensate_cancel_failed yc_id=%s",
                        new_yc_id,
                    )

                # If compensate succeeded, the rescheduled-from-old is now a
                # plain cancel (no new visit anywhere). Flip the old row from
                # RESCHEDULED to CANCELLED to keep analytics honest and emit
                # booking.cancelled with the SHARED correlation_id so
                # subscribers (Loyalty, analytics) see the composite outcome.
                #
                # When compensate FAILS (else branch below — we don't take any
                # action there), we intentionally LEAVE the old row in
                # RESCHEDULED. Customer still has a visit on YClients side
                # that we can't link to locally; marking the old row CANCELLED
                # would tell Loyalty «no visit happens» when in fact one might.
                # The critical-severity health alert below is the ops hook to
                # reconcile manually.
                if compensate_ok:
                    BookingRequest.all_tenants.filter(pk=booking.pk).update(
                        status=BookingRequest.Status.CANCELLED,
                    )
                    try:
                        eventbus_services.emit_booking_cancelled(
                            booking_id=str(booking.pk),
                            cancelled_by="system",
                            cancellation_reason="reschedule_local_create_failed",
                            cancelled_at=dj_timezone.now().isoformat(),
                            actor_type="system",
                            correlation_id=_reschedule_correlation_id,
                            tenant=tenant,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "booking.reschedule.recovery_cancel_emit_failed booking=%s",
                            booking.pk,
                        )

                # Always audit + alert — ops needs visibility regardless of
                # whether compensate succeeded.
                write_audit(
                    "bookings.reschedule.local_create_failed",
                    target="BookingSkill",
                    payload={
                        "tenant_id": tenant_id,
                        "old_record_id": record_id,
                        "new_yc_record_id": new_yc_id,
                        "compensate_succeeded": compensate_ok,
                        "error": str(exc)[:200],
                    },
                )
                try:
                    eventbus_services.emit(
                        "system.module.health.degraded",
                        {
                            "module_name": "booking.execute_reschedule",
                            "severity": "critical" if not compensate_ok else "warning",
                            "metric": (
                                f"split_brain_unresolved_yc_id={new_yc_id}"
                                if not compensate_ok
                                else f"split_brain_recovered_yc_id={new_yc_id}"
                            ),
                        },
                        actor_type="system",
                        tenant=tenant,
                    )
                except Exception:  # noqa: BLE001 — alert never breaks the tool
                    logger.exception("booking.reschedule.alert_emit_failed")
                _audit_tool(
                    tenant_id=tenant_id,
                    tool="execute_reschedule",
                    outcome=(
                        "split_brain_recovered" if compensate_ok else "split_brain_unresolved"
                    ),
                )
                return BookingToolResult(error="booking_reschedule_local_create_failed")

        _schedule_reminders(
            tenant=tenant,
            bot_user=bot_user,
            yc_id=new_yc_id,
            visit_at_dt=visit_at_dt,
            master_name=master_name,
            service_name=service_name,
        )

    _audit_tool(tenant_id=tenant_id, tool="execute_reschedule", outcome="ok")
    write_audit(
        EVENT_BOOKING_RESCHEDULED,
        target="BookingSkill",
        payload={
            "tenant_id": tenant_id,
            "old_record_id": record_id,
            "new_record_id": record.record_id,
            "new_datetime": new_datetime,
        },
    )
    confirmation = ConfirmationResult(
        ok=True,
        record_id=record.record_id,
        visit_at=visit_at_iso,
        master_name=master_name,
        service_name=service_name,
    )
    text = f"Перенесла запись на {new_datetime}. Если что-то ещё нужно — пишите!"
    return BookingToolResult(text=text, confirmation=confirmation)


def _find_user_booking(
    *,
    tenant: Any,
    bot_user: Any,
    record_id: int | str,
) -> BookingRequest | None:
    """Look up a :class:`BookingRequest` owned by ``bot_user`` for ``record_id``.

    Uses the ``yclients_record_id=<id>`` comment-marker convention —
    same key the admin webhook + show_my_bookings use. Returns the
    first match scoped to ``(tenant, bot_user)`` so a cross-tenant
    leak is impossible by construction (tenant filter is non-bypassable).

    Filters out terminal-cancelled rows so a user can't re-cancel an
    already-cancelled booking — the LLM would otherwise see the row
    in show_my_bookings and accidentally route to cancel_booking.

    Skills retro hotfix #1 (cont'd): anchor on the pipe-space-prefixed
    marker rather than the bare ``yclients_record_id=<id>`` substring.
    Both legitimate writers ship the marker right after ``" | "``:
      - skill:         ``"Bot booking | yclients_record_id=<id>"``
      - admin webhook: ``"YClients admin booking | yclients_record_id=<id>"``
    The pipe character is structurally reserved in our comment format,
    so an attacker can't inject `` | yclients_record_id=<victim-id>``
    via a free-text service_name / client_name. This closes the reader-
    side half of the spoof vector the previous PR closed for writers.
    """
    # Flag OFF: re-coerce to int (byte-for-byte YClients normalisation). Flag
    # ON: the marker carries the Ayla appointment UUID written at confirm time.
    rid = record_id if _booking_via_ayla() else int(record_id)
    marker = f" | yclients_record_id={rid}"
    return (
        BookingRequest.all_tenants.filter(
            tenant=tenant,
            bot_user=bot_user,
            comment__contains=marker,
        )
        .exclude(status=BookingRequest.Status.CANCELLED)
        .exclude(status=BookingRequest.Status.RESCHEDULED)
        .first()
    )


def _find_yclients_user_record(*, client: Any, record_id: int | str) -> Any:
    """Look up a UserRecord from YClients ``get_user_records`` by id.

    Used by :func:`reschedule_booking` to retrieve the staff + service
    pair needed for the new ``create_record`` call. Returns ``None`` if
    the API is unreachable OR the id isn't in the user's records (the
    record may be salon-side only, or the user_token doesn't have
    access).

    Perf note (skills retro residual #9): YClients exposes only
    ``GET /user/records`` — no single-record-by-id endpoint. We iterate
    the full list and return on first match. Customer record history is
    typically dozens of entries (Q-CO5 — user_token is per-customer), so
    the O(N) is bounded by individual customer activity, not platform-
    wide volume. If a power-user customer's record list ever grows to
    hundreds and reschedule latency regresses, file an enhancement on
    the YClients integration to cache the records-by-id map per tenant
    request.
    """
    try:
        records = client.get_user_records()
    except (YClientsUnavailableError, YClientsAPIError):
        return None
    for rec in records:
        if getattr(rec, "id", None) == record_id:
            return rec
    return None


# ---------------------------------------------------------------------------
# Tool 6: show_my_bookings
# ---------------------------------------------------------------------------


def _show_my_bookings_ayla(
    *,
    tenant: Any,
    bot_user: Any,
    tenant_id: str,
    now: datetime,
) -> BookingToolResult:
    """``show_my_bookings`` over the Ayla mirror (flag ON).

    The CONFIRMED ``BookingRequest`` rows carry the display names + the
    ``yclients_record_id=<uuid>`` marker; the matching ``RemoteBookingProxy``
    row supplies the live ``start_at`` + status (so cancelled / past bookings
    drop out). Same shape as the flag-OFF path, mirror in place of YClients.
    """
    from apps.booking.models import RemoteBookingProxy

    rows = list(
        BookingRequest.all_tenants.filter(
            tenant=tenant,
            bot_user=bot_user,
            status=BookingRequest.Status.CONFIRMED,
        ).order_by("-created_at")[:20]
    )
    proxies = {
        str(p.appointment_id): p
        for p in RemoteBookingProxy.all_tenants.filter(tenant=tenant, bot_user=bot_user)
    }

    bookings: list[BookingRow] = []
    for row in rows:
        appt = _extract_yc_id(row.comment)  # UUID string under flag ON
        proxy = proxies.get(str(appt)) if appt else None
        visit_iso = ""
        if proxy is not None:
            if proxy.status == RemoteBookingProxy.Status.CANCELLED:
                continue
            if proxy.start_at is not None:
                if proxy.start_at < now:
                    continue
                visit_iso = proxy.start_at.isoformat()
        bookings.append(
            BookingRow(
                record_id=str(appt) if appt else "",
                visit_at=visit_iso,
                master_name=row.master_name,
                service_name=row.service_name,
                status="CONFIRMED",
            )
        )

    _audit_tool(
        tenant_id=tenant_id,
        tool="show_my_bookings",
        outcome="ok",
        extra={"count": len(bookings)},
    )
    return BookingToolResult(text=_format_bookings_text(bookings), bookings=bookings)


def show_my_bookings(
    *,
    client: Any,
    tenant: Any,
    bot_user: Any,
) -> BookingToolResult:
    """List the bot_user's upcoming CONFIRMED bookings.

    Local source: ``BookingRequest`` rows for ``(tenant, bot_user)``
    with ``created_at`` recent enough and a yclients marker in the
    comment. Augmented when possible with live data from
    :meth:`YClientsAPI.get_user_records`.
    """
    tenant_id = str(getattr(tenant, "id", ""))
    now = dj_timezone.now()

    # Flag ON: the canonical mirror (RemoteBookingProxy) is the live source —
    # the YClients records endpoint isn't populated under Ayla. Names still
    # come from the BookingRequest billing row (the mirror is PII-free).
    if _booking_via_ayla():
        return _show_my_bookings_ayla(
            tenant=tenant, bot_user=bot_user, tenant_id=tenant_id, now=now
        )

    # Skills retro residual #10: exclude terminal-cancelled and
    # replaced-by-reschedule rows. Without this filter the LLM sees
    # «ghost» bookings in show_my_bookings → the user gets a confusing
    # list that mixes live + dead rows, and a subsequent cancel/reschedule
    # tool call routed at a ghost row dies inside _find_user_booking
    # (which already filters terminal statuses) with «not found».
    # CANCEL_REQUESTED / RESCHEDULE_REQUESTED stay excluded too — those
    # are transient states for the in-flight 2-button preview, not
    # something to surface as «your upcoming booking».
    #
    # Reader/writer asymmetry note: _find_user_booking (the cancel/
    # reschedule reader) excludes only CANCELLED + RESCHEDULED — it
    # accepts CANCEL_REQUESTED / RESCHEDULE_REQUESTED so the same record
    # the customer is mid-confirming can be actioned by ✅ tap. This is
    # the intended split: show_my_bookings is the LLM's «what's live»
    # view (no transient rows); _find_user_booking is the gate executor's
    # «can the customer act on this id» view (transient rows included).
    rows = list(
        BookingRequest.all_tenants.filter(
            tenant=tenant,
            bot_user=bot_user,
            status=BookingRequest.Status.CONFIRMED,
        ).order_by("-created_at")[:20]
    )

    # We don't store visit_at on BookingRequest — pull live data
    # from YClients when available to know which bookings are still
    # upcoming. Fail-soft on network errors.
    live_records: dict[int | str, Any] = {}
    try:
        for rec in client.get_user_records():
            live_records[rec.id] = rec
    except (YClientsUnavailableError, YClientsAPIError):
        # Empty live_records → fall back to comment-marker only.
        pass

    bookings: list[BookingRow] = []
    for row in rows:
        yc_id = _extract_yc_id(row.comment)
        live = live_records.get(yc_id) if yc_id else None
        visit_iso = live.datetime if live is not None else ""
        if live is not None:
            dt = _parse_iso_datetime(visit_iso)
            if dt is not None and dt < now:
                continue
        bookings.append(
            BookingRow(
                record_id=yc_id or 0,
                visit_at=visit_iso,
                master_name=row.master_name,
                service_name=row.service_name,
                status="CONFIRMED",
            )
        )

    _audit_tool(
        tenant_id=tenant_id,
        tool="show_my_bookings",
        outcome="ok",
        extra={"count": len(bookings)},
    )
    text = _format_bookings_text(bookings)
    return BookingToolResult(text=text, bookings=bookings)


def _format_bookings_text(bookings: list[BookingRow]) -> str:
    if not bookings:
        return "У вас пока нет предстоящих записей."
    lines = ["Ваши предстоящие записи:"]
    for b in bookings[:5]:
        parts = [b.service_name or "—"]
        if b.master_name:
            parts.append(f"с {b.master_name}")
        if b.visit_at:
            parts.append(f"в {b.visit_at}")
        lines.append("• " + " ".join(parts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 7: calc_price (B6 / DRF-842)
# ---------------------------------------------------------------------------


def calc_price(
    *,
    tenant: Any,
    arguments: dict[str, Any],
    allowed_service_ids: AbstractSet[int | str],
    service_lookup: dict[Any, str],
) -> BookingToolResult:
    """Quote a service price, optionally applying a promo code.

    Args:
      tenant: current :class:`apps.tenancy.models.Tenant`. Used to
              scope both the catalog lookup (for ``price_from``) and
              the promo lookup.
      arguments: LLM-supplied ``{service_id, promo_code?}``.
      allowed_service_ids: pre-fetched set of valid YClients service
                           ids on this tenant. An LLM-emitted id not
                           in this set returns
                           ``error="price_invalid_service_id"`` so the
                           skill emits a handoff (anti-hallucination
                           guard — mirrors confirm_booking).
      service_lookup: id → display name map, used to render the reply
                      and to fall back when the catalog row is missing
                      a name.

    Returns a :class:`BookingToolResult` with ``price`` populated on
    success. ``error="price_invalid_service_id"`` triggers a handoff
    at the skill layer. Other promo failures (not_found / inactive /
    expired / etc.) are NOT errors — they're part of the answer, see
    ``promo_status`` on the :class:`CalcPriceResult`.
    """
    from apps.catalog.models import CatalogService

    service_id = _coerce_id(arguments.get("service_id"))
    raw_promo = arguments.get("promo_code")
    promo_code = str(raw_promo).strip() if raw_promo else ""

    tenant_id = str(getattr(tenant, "id", ""))

    if service_id is None or service_id not in allowed_service_ids:
        logger.info(
            "booking.calc_price.invalid_service_id service_id=%s allowed=%s",
            service_id,
            sorted(allowed_service_ids),
        )
        _audit_tool(
            tenant_id=tenant_id,
            tool="calc_price",
            outcome="invalid_service_id",
        )
        return BookingToolResult(error="price_invalid_service_id")

    # Resolve the catalog mirror row for pricing. The mirror carries two keys:
    # the legacy YClients int ``external_id`` (flag OFF) and the canonical Ayla
    # ``ayla_service_id`` UUID (flag ON, same key the health-check gate grounds
    # on). Either path falls through to the "price by request" branch below on
    # a miss — never crash on ``int(<uuid>)``.
    if _booking_via_ayla():
        ayla_uuid = _as_uuid(service_id)
        catalog_row = (
            None
            if ayla_uuid is None
            else (
                CatalogService.all_tenants.filter(
                    tenant=tenant,
                    ayla_service_id=ayla_uuid,
                )
                .only("id", "name", "price_from")
                .first()
            )
        )
    else:
        catalog_row = (
            CatalogService.all_tenants.filter(
                tenant=tenant,
                external_id=int(service_id),
            )
            .only("id", "name", "price_from")
            .first()
        )

    if catalog_row is None:
        # YClients knows the service but the catalog mirror doesn't.
        # The catalog sync runs every 15 min — we may be in the gap
        # right after a salon added a new service in mysite. Surface
        # the bot's display name (from service_lookup) and treat as
        # "цена по запросу" so the LLM doesn't confidently quote a
        # made-up price.
        service_name = service_lookup.get(service_id, "")
        result = CalcPriceResult(
            service_name=service_name,
            original_price=None,
            final_price=None,
            discount_percent=None,
            promo_status="not_supplied" if not promo_code else "not_found",
        )
        text = _format_price_text(result)
        _audit_tool(
            tenant_id=tenant_id,
            tool="calc_price",
            outcome="catalog_missing",
            extra={"service_id": service_id},
        )
        return BookingToolResult(text=text, price=result)

    service_name = catalog_row.name or service_lookup.get(service_id, "")
    base_price: Decimal | None = catalog_row.price_from

    # ── No promo case ──────────────────────────────────────────────
    if not promo_code:
        result = CalcPriceResult(
            service_name=service_name,
            original_price=base_price,
            final_price=base_price,
            discount_percent=None,
            promo_status="not_supplied",
        )
        text = _format_price_text(result)
        _audit_tool(
            tenant_id=tenant_id,
            tool="calc_price",
            outcome="ok_no_promo",
            extra={"service_id": service_id},
        )
        write_audit(
            EVENT_BOOKING_PRICE_QUOTED,
            target="BookingSkill",
            payload={
                "tenant_id": tenant_id,
                "service_id": service_id,
                "promo": "",
                "promo_status": "not_supplied",
            },
        )
        return BookingToolResult(text=text, price=result)

    # ── Promo case ─────────────────────────────────────────────────
    validation = validate_promo(
        tenant=tenant,
        code=promo_code,
        service_id=catalog_row.id,
    )

    discount: int | None = None
    final_price: Decimal | None = base_price
    if validation.ok and validation.promo is not None and base_price is not None:
        discount = validation.promo.discount_percent
        # Discount math at Decimal precision: (100 - discount) / 100
        # is exact for integer percent. Quantise to 2 dp at format
        # time, not here, so downstream consumers (audit, future
        # checkout) see the unrounded result.
        multiplier = (Decimal(100) - Decimal(discount)) / Decimal(100)
        final_price = (base_price * multiplier).quantize(Decimal("0.01"))
    elif validation.ok and validation.promo is not None and base_price is None:
        # Promo valid but base price unknown — discount is meaningless,
        # report ok=False semantics (no discount applied) but keep the
        # row's percent for transparency.
        discount = validation.promo.discount_percent

    result = CalcPriceResult(
        service_name=service_name,
        original_price=base_price,
        final_price=final_price,
        discount_percent=discount,
        promo_status=validation.reason,
    )
    text = _format_price_text(result)
    _audit_tool(
        tenant_id=tenant_id,
        tool="calc_price",
        outcome="ok",
        extra={
            "service_id": service_id,
            "promo_status": validation.reason,
        },
    )
    write_audit(
        EVENT_BOOKING_PRICE_QUOTED,
        target="BookingSkill",
        payload={
            "tenant_id": tenant_id,
            "service_id": service_id,
            "promo": promo_code.upper(),
            "promo_status": validation.reason,
        },
    )
    return BookingToolResult(text=text, price=result)


def _format_price_text(result: CalcPriceResult) -> str:
    """Render a Russian price quote for the deterministic fallback path.

    The skill's second LLM call MAY rephrase; this is what the channel
    adapter shows when the LLM returns empty. Wording per the B6 spec:

    * No promo, price known          → "1 500 ₽".
    * Valid promo applied            → "1 500 ₽ → 1 350 ₽ (промокод MAY10, −10%)".
    * Promo not_found / inactive     → "Не нашла такой промокод — могу посчитать без него?"
    * Promo not_started              → "Этот промокод начнёт действовать ..."
    * Promo expired                  → "Срок действия промокода истёк."
    * Promo wrong_service            → "Промокод не подходит к этой услуге."
    * price_from is None             → "Точную цену озвучит администратор — это услуга по индивидуальному прайсу."

    Strings are short on purpose — the LLM rephrases freely on top.
    """
    name = result.service_name or "услуга"

    if result.original_price is None:
        return f"{name}: точную цену озвучит администратор — это услуга по индивидуальному прайсу."

    base_str = format_rub(result.original_price)

    status = result.promo_status
    if status == "not_supplied":
        return f"{name}: {base_str}."
    if status == "ok" and result.final_price is not None and result.discount_percent is not None:
        final_str = format_rub(result.final_price)
        return f"{name}: {base_str} → {final_str} (промокод, −{result.discount_percent}%)."
    if status in ("not_found", "inactive"):
        return f"Не нашла такой промокод — могу посчитать без него? {name}: {base_str}."
    if status == "not_started":
        return f"Этот промокод начнёт действовать позже. Сейчас {name}: {base_str}."
    if status == "expired":
        return f"Срок действия промокода истёк. {name}: {base_str}."
    if status == "wrong_service":
        return f"Промокод не подходит к этой услуге. {name}: {base_str}."
    # Fallback — should not be reached, but keeps mypy happy on the
    # exhaustive-match case (PromoStatus is a Literal).
    return f"{name}: {base_str}."


# ---------------------------------------------------------------------------
# Tool 8: buy_certificate (B7 / DRF-843)
# ---------------------------------------------------------------------------


def buy_certificate(
    *,
    tenant: Any,
    bot_user: Any,
    arguments: dict[str, Any],
) -> BookingToolResult:
    """Request a checkout URL from Ayla for a gift certificate.

    Phase 0 / #427 + ADR-0009 §Hard rule #5: bot-platform no longer
    talks to YooKassa directly. The skill calls Ayla djangoproject's
    ``POST /api/v1/payments/create`` — Ayla persists the canonical
    Payment row, drives the YooKassa lifecycle, and owns the webhook.
    bot-platform's role shrinks to: parse the user's intent, ask
    Ayla, render the returned checkout URL via an inline button.

    Args:
      tenant: current :class:`apps.tenancy.models.Tenant`. Used for
              audit only — bot-platform no longer writes a payment
              row scoped to the tenant (canonical state lives in
              Ayla, which is multi-tenant-aware on its own side).
      bot_user: current :class:`apps.identity.models.BotUser` — the
              buyer. Still recorded in the audit row.
      arguments: LLM-supplied ``{amount_rub, recipient_name?, buyer_email?}``.

    Returns a :class:`BookingToolResult` with ``certificate`` populated.
    On success ``keyboard`` carries an inline ``💳 Оплатить`` URL
    button (the channel adapter renders it natively).

    Error contract:

    * ``amount_out_of_range`` — clarification, no Ayla call.
    * ``certificate_provider_failure`` — Ayla payments endpoint
      raised (5xx / 4xx / unreachable). Audit row written so the
      operator can see the attempt.

    Validation order:

    1. Parse + range-check the amount BEFORE calling Ayla. An
       out-of-range value MUST NOT trigger any HTTP call.
    2. Generate idempotence_key, call Ayla.
    3. On success: build the keyboard from the returned URL.
    4. On failure: audit + return handoff slug.

    Compared to the prior YooKassa-direct shape, the Order row write
    is GONE — per ADR-0009 §Hard rule #1 (no duplicate canonical
    state), Ayla owns the Payment row. The legacy YooKassa webhook
    + ``apps/orders/tasks.py`` survive until #428 to drain any
    in-flight YooKassa payments during the safety window; new
    payments after this PR lands flow exclusively through Ayla.
    """
    # Local imports — keeps Django-app-load cycles narrow and lets
    # tests substitute the singleton via reset_ayla_payments_client().
    from django.conf import settings

    from apps.bookings.keyboards import url_button
    from apps.integrations.ayla_payments import (
        AylaPaymentsAPIError,
        AylaPaymentsUnavailableError,
        get_ayla_payments_client,
    )

    tenant_id = str(getattr(tenant, "id", ""))

    # ── 0. Feature flag (B2) ────────────────────────────────────────
    # CERTIFICATE_PAYMENT_ENABLED defaults False per founder verdict
    # 2026-05-30 (memory ``project_certificate_payment_post_pilot``).
    # When off, short-circuit BEFORE any Ayla call and before amount
    # parsing — the disabled path costs zero IO and emits a single
    # audit row so operators can see attempted use during the freeze.
    # ``get_active_booking_tool_specs()`` already hides the spec from
    # the LLM tool list; this guard is defence-in-depth for keyword
    # fallbacks, replay paths, and direct programmatic callers.
    if not getattr(settings, "CERTIFICATE_PAYMENT_ENABLED", False):
        _audit_tool(
            tenant_id=tenant_id,
            tool="buy_certificate",
            outcome="disabled",
        )
        return BookingToolResult(
            certificate=BuyCertificateResult(ok=False, error="certificate_disabled"),
            error="certificate_disabled",
            text=(
                # Deliberately no launch ETA — the founder freeze is
                # contingent on legal review (ФЗ-54 / ст. 487 ГК РФ /
                # ФЗ-2300-1), so any commitment here would overpromise.
                "Подарочные сертификаты сейчас недоступны. Могу помочь с записью на услугу?"
            ),
        )

    # ── 1. Amount parsing + range guard ─────────────────────────────
    amount = _coerce_decimal(arguments.get("amount_rub"))
    if amount is None:
        _audit_tool(
            tenant_id=tenant_id,
            tool="buy_certificate",
            outcome="invalid_amount",
        )
        return BookingToolResult(
            certificate=BuyCertificateResult(ok=False, error="amount_out_of_range"),
            error="amount_out_of_range",
            text=(
                "Сумма сертификата должна быть числом от 500 до 100000 ₽. Какую сумму подобрать?"
            ),
        )
    if amount < CERTIFICATE_AMOUNT_MIN or amount > CERTIFICATE_AMOUNT_MAX:
        _audit_tool(
            tenant_id=tenant_id,
            tool="buy_certificate",
            outcome="amount_out_of_range",
            extra={"amount": str(amount)},
        )
        return BookingToolResult(
            certificate=BuyCertificateResult(ok=False, error="amount_out_of_range"),
            error="amount_out_of_range",
            text=(
                f"Сумма должна быть от {CERTIFICATE_AMOUNT_MIN:.0f} "
                f"до {CERTIFICATE_AMOUNT_MAX:.0f} ₽. Какую сумму подобрать?"
            ),
        )

    recipient_name = str(arguments.get("recipient_name") or "").strip()
    buyer_email = str(arguments.get("buyer_email") or "").strip()
    description = f"Сертификат на {amount:.0f} ₽" + (
        f" для {recipient_name}" if recipient_name else ""
    )

    # ── 2. Ayla payments call ────────────────────────────────────────
    from uuid import uuid4

    idempotence_key = uuid4()
    client = get_ayla_payments_client()
    try:
        result = client.create_payment(
            amount_rub=amount,
            description=description[:128],
            idempotence_key=idempotence_key,
            recipient_name=recipient_name,
            buyer_email=buyer_email,
            kind="certificate",
        )
    except (AylaPaymentsUnavailableError, AylaPaymentsAPIError) as exc:
        logger.warning(
            "buy_certificate.provider_failure idem=%s err=%s",
            idempotence_key,
            type(exc).__name__,
        )
        _audit_tool(
            tenant_id=tenant_id,
            tool="buy_certificate",
            outcome="provider_failure",
            extra={
                "idempotence_key": str(idempotence_key),
                "exception_type": type(exc).__name__,
            },
        )
        write_audit(
            EVENT_CERTIFICATE_CHECKOUT_FAILED,
            target="BookingSkill",
            payload={
                "tenant_id": tenant_id,
                "idempotence_key": str(idempotence_key),
                "exception_type": type(exc).__name__,
            },
        )
        return BookingToolResult(
            certificate=BuyCertificateResult(
                ok=False,
                amount_rub=amount,
                error="certificate_provider_failure",
            ),
            error="certificate_provider_failure",
        )

    _audit_tool(
        tenant_id=tenant_id,
        tool="buy_certificate",
        outcome="ok",
        extra={
            "payment_id": result.payment_id,
            "amount": str(amount),
            "test_mode": result.test,
        },
    )
    write_audit(
        EVENT_CERTIFICATE_CHECKOUT_REQUESTED,
        target="BookingSkill",
        payload={
            "tenant_id": tenant_id,
            # Audit payload MUST NOT include the bearer token. The Ayla
            # payment_id is a public-facing identifier (mirrors the
            # YooKassa-era external_payment_id field).
            "payment_id": result.payment_id,
            "amount_rub": str(amount),
            "test_mode": result.test,
        },
    )

    text = (
        f"Сертификат на {amount:.0f} ₽ готов к оплате. "
        "Нажмите кнопку ниже, чтобы перейти к безопасной оплате."
    )
    keyboard = url_button("💳 Оплатить", result.checkout_url)

    return BookingToolResult(
        text=text,
        certificate=BuyCertificateResult(
            ok=True,
            # ``order_id`` is the public DTO field name — kept for
            # backwards compatibility with the channel adapter +
            # rendering layer. Populated from Ayla's ``payment_id``
            # now that bot-platform no longer owns an Order row.
            order_id=result.payment_id,
            amount_rub=amount,
            checkout_url=result.checkout_url,
        ),
        keyboard=keyboard,
    )


def _coerce_decimal(value: Any) -> Decimal | None:
    """Convert an arbitrary JSON-decoded number into a Decimal.

    Handles int / float / str / Decimal inputs. Floats are routed
    through ``str()`` first so we don't pick up float-imprecision
    noise (``Decimal(1.1)`` is a ~17-digit horror; ``Decimal(str(1.1))``
    is the expected ``1.1``). Returns None on any parse error.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # bool is a subclass of int — explicit guard so True doesn't
        # silently become Decimal("1").
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(str(value).strip())
    except (TypeError, ValueError, ArithmeticError):
        return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def build_master_lookup(masters: list[Service] | list[Staff]) -> dict[int | str, str]:
    """``id → human name`` map for snapshot writes (id type follows the flag)."""
    out: dict[int | str, str] = {}
    for m in masters:
        name = getattr(m, "name", "") or getattr(m, "title", "")
        out[_id_key(m.id)] = str(name)
    return out


def build_service_lookup(services: list[Service]) -> dict[int | str, str]:
    """``id → title`` map for service display name lookup (id type follows the flag)."""
    return {_id_key(s.id): s.title for s in services}


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _booking_via_ayla() -> bool:
    """True when the booking skill is routed through the Ayla REST bridge.

    S1 / #1016 PR-2. Flag OFF (default): YClients, int catalog/record ids.
    Flag ON: Ayla canonical REST, UUID-string ids. Every id-shaped helper
    below branches on this so the flag-OFF path stays byte-for-byte YClients.
    """
    from django.conf import settings

    return bool(getattr(settings, "BOOKING_VIA_AYLA_REST", False))


def _id_key(value: Any) -> int | str:
    """Normalise a *provider-supplied* id for allow-sets / lookup maps.

    Flag OFF → ``int`` (YClients); flag ON → ``str`` (Ayla UUID). Provider
    ids are well-formed, so unlike :func:`_coerce_id` this never returns
    ``None``. Mirrors the old inline ``int(s.id)`` on the flag-OFF path.
    """
    if _booking_via_ayla():
        return str(value)
    return int(value)


def _coerce_id(value: Any) -> int | str | None:
    """Flag-aware coercion of an *LLM/payload-supplied* id.

    Flag OFF: identical to :func:`_coerce_int` (``int | None``) — preserves
    byte-for-byte YClients behaviour and the anti-hallucination semantics.
    Flag ON: the trimmed UUID string, or ``None`` for empty/missing input.
    """
    if _booking_via_ayla():
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    return _coerce_int(value)


def _ayla_handle(record: BookingRecord) -> str:
    """Canonical Ayla appointment UUID carried in a create/reschedule result.

    The provider adapter stows it in ``record.raw["ayla_appointment_id"]``
    (``record_id`` itself is 0 on the Ayla path). Empty string when absent.
    """
    return str(record.raw.get("ayla_appointment_id") or "")


def _confirm_record_handle(record: BookingRecord) -> int | str:
    """The id the booking flow keys on for markers / reminders / lookups.

    Flag OFF: the YClients int record id (unchanged). Flag ON: the canonical
    Ayla appointment UUID string.
    """
    if _booking_via_ayla():
        return _ayla_handle(record)
    return record.record_id


def _as_uuid(value: Any) -> Any:
    """Coerce to ``uuid.UUID`` for the proxy's UUIDField, tolerating junk → None."""
    if not value:
        return None
    import uuid as _uuid

    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _upsert_remote_booking_proxy(
    *,
    tenant: Any,
    bot_user: Any,
    record: BookingRecord,
    start_at: datetime | None,
) -> None:
    """Mirror a CONFIRMED Ayla appointment onto :class:`RemoteBookingProxy`.

    ADR-0009: Ayla owns the canonical booking; bot-platform keeps this thin
    mirror for reminder math + RFM/sentiment fan-out. Used by confirm and
    (native) reschedule — both land the appointment in CONFIRMED. Best-effort:
    the canonical row already exists in Ayla, so a mirror-write failure must
    NOT fail the customer-facing action (the next ``booking.*`` event from
    Ayla reconciles it). No-op on the flag-OFF (YClients) path.
    """
    if not _booking_via_ayla() or start_at is None:
        return
    appt = _ayla_handle(record)
    if not appt:
        return
    raw = record.raw
    end_at = _parse_iso_datetime(str(raw.get("end_at") or "")) or start_at
    try:
        from apps.booking.models import RemoteBookingProxy

        # S5-LOW: scope the lookup by (appointment_id, tenant), not
        # appointment_id alone. appointment_id is the PK (globally unique), so
        # a cross-tenant appointment_id collision surfaces as an IntegrityError
        # (caught by the best-effort guard below) instead of silently
        # overwriting another tenant's mirror row.
        RemoteBookingProxy.all_tenants.update_or_create(
            appointment_id=_as_uuid(appt),
            tenant=tenant,
            defaults={
                "bot_user": bot_user,
                "start_at": start_at,
                "end_at": end_at,
                "status": RemoteBookingProxy.Status.CONFIRMED,
                "source": RemoteBookingProxy.Source.AUTOMATION,
                "service_id": _as_uuid(raw.get("service_id")),
                "specialist_id": _as_uuid(raw.get("specialist_id")),
            },
        )
    except Exception:  # noqa: BLE001 — mirror write is best-effort (see docstring)
        logger.exception("booking.proxy.upsert_failed appt=%s", appt)


def _proxy_master_service(*, tenant: Any, record_id: int | str) -> tuple[str | None, str | None]:
    """Resolve ``(specialist_id, service_id)`` from the mirror (flag ON).

    Returns ``(None, None)`` when no mirror row exists — the caller maps that
    to ``record_not_found`` so a reschedule can't proceed without the pair.
    """
    appt = _as_uuid(record_id)
    if appt is None:
        return None, None
    try:
        from apps.booking.models import RemoteBookingProxy

        row = (
            RemoteBookingProxy.all_tenants.filter(tenant=tenant, appointment_id=appt)
            .only("service_id", "specialist_id")
            .first()
        )
    except Exception:  # noqa: BLE001 — defensive; caller treats None as not-found
        return None, None
    if row is None:
        return None, None
    spec = str(row.specialist_id) if row.specialist_id else None
    svc = str(row.service_id) if row.service_id else None
    return spec, svc


def _mirror_cancel(*, tenant: Any, record_id: int | str) -> None:
    """Flip the mirror row for ``record_id`` to CANCELLED (flag ON, best-effort)."""
    if not _booking_via_ayla():
        return
    appt = _as_uuid(record_id)
    if appt is None:
        return
    try:
        from apps.booking.models import RemoteBookingProxy

        RemoteBookingProxy.all_tenants.filter(tenant=tenant, appointment_id=appt).update(
            status=RemoteBookingProxy.Status.CANCELLED,
        )
    except Exception:  # noqa: BLE001 — mirror write is best-effort
        logger.exception("booking.proxy.cancel_failed appt=%s", record_id)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dj_timezone.make_aware(dt)
    return dt


def _extract_yc_id(comment: str) -> int | str | None:
    """Parse the ``yclients_record_id=<id>`` marker out of a comment.

    Flag OFF: ``<id>`` is YClients digits → ``int``. Flag ON: ``<id>`` is the
    Ayla appointment UUID written by :func:`execute_confirm` → ``str`` read up
    to the next separator (space / pipe). The marker key name is retained
    under flag ON for one reader/writer convention across both providers.
    """
    if not comment:
        return None
    marker = "yclients_record_id="
    idx = comment.find(marker)
    if idx < 0:
        return None
    rest = comment[idx + len(marker) :]
    if _booking_via_ayla():
        token = ""
        for ch in rest:
            if ch in " |\n\t":
                break
            token += ch
        return token or None
    digits = ""
    for ch in rest:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _audit_tool(
    *,
    tenant_id: str,
    tool: str,
    outcome: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "tool": tool,
        "outcome": outcome,
    }
    if extra:
        payload.update(extra)
    write_audit(
        EVENT_BOOKING_TOOL_INVOKED,
        target="BookingSkill",
        payload=payload,
    )
