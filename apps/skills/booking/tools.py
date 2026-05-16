"""Booking-skill tool specs + handlers (DRF-839 / Phase 1 / B3 + B5).

Six LLM-callable functions:

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
from typing import Any
from uuid import UUID

from django.utils import timezone as dj_timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingRequest, PendingBookingAction
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


BOOKING_TOOL_SPECS: list[dict[str, Any]] = [
    SHOW_MASTERS_TOOL_SPEC,
    SHOW_SLOTS_TOOL_SPEC,
    CONFIRM_BOOKING_TOOL_SPEC,
    CANCEL_BOOKING_TOOL_SPEC,
    RESCHEDULE_BOOKING_TOOL_SPEC,
    SHOW_MY_BOOKINGS_TOOL_SPEC,
]


# Audit slugs.
EVENT_BOOKING_TOOL_INVOKED = "booking.tool_invoked"
EVENT_BOOKING_CONFIRMED = "booking.confirmed"
EVENT_BOOKING_CONFIRM_FAILED = "booking.confirm_failed"
EVENT_BOOKING_PREVIEW = "booking.preview"
EVENT_BOOKING_CANCELLED = "booking.cancelled"
EVENT_BOOKING_CANCEL_FAILED = "booking.cancel_failed"
EVENT_BOOKING_RESCHEDULED = "booking.rescheduled"
EVENT_BOOKING_RESCHEDULE_PARTIAL = "booking.reschedule_partial_failure"


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
    record_id: int = 0
    visit_at: str = ""
    master_name: str = ""
    service_name: str = ""
    error: str = ""


@dataclass(frozen=True)
class BookingRow:
    """Trimmed booking DTO for :func:`show_my_bookings`."""

    record_id: int
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
    service_id = _coerce_int(arguments.get("service_id"))
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


def _relevance_score(staff: Staff, needle: str, service_id: int | None) -> float:
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
    allowed_master_ids: set[int],
) -> BookingToolResult:
    """List available time slots for ``master_id``.

    Validates ``master_id`` against ``allowed_master_ids`` (the set
    populated by a prior :func:`show_masters` call). Returns
    ``error="invalid_master_id"`` when the LLM hallucinated.
    """
    master_id = _coerce_int(arguments.get("master_id"))
    if master_id is None or master_id not in allowed_master_ids:
        logger.info(
            "booking.show_slots.invalid_master_id master_id=%s allowed=%s",
            master_id,
            sorted(allowed_master_ids),
        )
        _audit_tool(tenant_id=tenant_id, tool="show_slots", outcome="invalid_master_id")
        return BookingToolResult(error="invalid_master_id")

    service_id = _coerce_int(arguments.get("service_id"))
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
    allowed_master_ids: set[int],
    allowed_service_ids: set[int],
    master_lookup: dict[int, str],
    service_lookup: dict[int, str],
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
    master_id = _coerce_int(arguments.get("master_id"))
    service_id = _coerce_int(arguments.get("service_id"))
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
    master_id = _coerce_int(payload.get("master_id"))
    service_id = _coerce_int(payload.get("service_id"))
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

    try:
        record: BookingRecord = client.create_record(
            staff_id=master_id,
            services=[service_id],
            datetime=slot_datetime,
            client_phone=phone,
            client_name=client_name,
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

    yc_id = str(record.record_id)
    yc_marker = f"yclients_record_id={yc_id}"

    existing = BookingRequest.all_tenants.filter(
        tenant=tenant,
        bot_user=bot_user,
        comment__contains=yc_marker,
    ).first()

    visit_at_dt = _parse_iso_datetime(slot_datetime)
    visit_at_iso = visit_at_dt.isoformat() if visit_at_dt else slot_datetime

    if existing is None:
        BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            category_name="",
            service_name=service_name or "—",
            master_name=master_name or "—",
            client_name=client_name,
            client_phone=phone,
            comment=f"Bot booking | {yc_marker}",
            source="bot",
            is_processed=False,
            status=BookingRequest.Status.CONFIRMED,
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
        record_id=record.record_id,
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
    record_id = _coerce_int(arguments.get("record_id"))
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
    record_id = _coerce_int(payload.get("record_id"))
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
    record_id = _coerce_int(arguments.get("record_id"))
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

    # Pull the original master/service from the YClients API so the
    # cancel-and-recreate uses the same master + service. We rely on
    # ``get_user_records`` for the staff/service link.
    user_record = _find_yclients_user_record(client=client, record_id=record_id)
    if user_record is None:
        _audit_tool(tenant_id=tenant_id, tool="reschedule_booking", outcome="record_not_found")
        return BookingToolResult(error="record_not_found")

    staff_id = _coerce_int((user_record.staff or {}).get("id"))
    services_list = user_record.services or []
    service_id = _coerce_int(services_list[0].get("id")) if services_list else None
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
    record_id = _coerce_int(payload.get("record_id"))
    new_datetime = str(payload.get("new_datetime") or "").strip()
    master_id = _coerce_int(payload.get("master_id"))
    service_id = _coerce_int(payload.get("service_id"))
    master_name = str(payload.get("master_name") or "")
    service_name = str(payload.get("service_name") or "")
    phone = str(payload.get("client_phone") or "")
    client_name = str(payload.get("client_name") or "Client")
    tenant_id = str(getattr(tenant, "id", ""))

    if record_id is None or master_id is None or service_id is None or not new_datetime:
        return BookingToolResult(error="invalid_payload")

    booking = _find_user_booking(tenant=tenant, bot_user=bot_user, record_id=record_id)
    if booking is None:
        _audit_tool(tenant_id=tenant_id, tool="execute_reschedule", outcome="invalid_record_id")
        return BookingToolResult(error="invalid_record_id")

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
            },
        )
        return BookingToolResult(error="booking_reschedule_partial_failure")

    new_yc_id = str(record.record_id)
    new_yc_marker = f"yclients_record_id={new_yc_id}"
    visit_at_dt = _parse_iso_datetime(new_datetime)
    visit_at_iso = visit_at_dt.isoformat() if visit_at_dt else new_datetime

    existing_new = BookingRequest.all_tenants.filter(
        tenant=tenant,
        bot_user=bot_user,
        comment__contains=new_yc_marker,
    ).first()
    if existing_new is None:
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
        )
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
    record_id: int,
) -> BookingRequest | None:
    """Look up a :class:`BookingRequest` owned by ``bot_user`` for ``record_id``.

    Uses the ``yclients_record_id=<id>`` comment-marker convention —
    same key the admin webhook + show_my_bookings use. Returns the
    first match scoped to ``(tenant, bot_user)`` so a cross-tenant
    leak is impossible by construction (tenant filter is non-bypassable).

    Filters out terminal-cancelled rows so a user can't re-cancel an
    already-cancelled booking — the LLM would otherwise see the row
    in show_my_bookings and accidentally route to cancel_booking.
    """
    marker = f"yclients_record_id={int(record_id)}"
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


def _find_yclients_user_record(*, client: Any, record_id: int) -> Any:
    """Look up a UserRecord from YClients ``get_user_records`` by id.

    Used by :func:`reschedule_booking` to retrieve the staff + service
    pair needed for the new ``create_record`` call. Returns ``None`` if
    the API is unreachable OR the id isn't in the user's records (the
    record may be salon-side only, or the user_token doesn't have
    access).
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

    rows = list(
        BookingRequest.all_tenants.filter(
            tenant=tenant,
            bot_user=bot_user,
        ).order_by("-created_at")[:20]
    )

    # We don't store visit_at on BookingRequest — pull live data
    # from YClients when available to know which bookings are still
    # upcoming. Fail-soft on network errors.
    live_records: dict[int, Any] = {}
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
# Shared helpers
# ---------------------------------------------------------------------------


def build_master_lookup(masters: list[Service] | list[Staff]) -> dict[int, str]:
    """``id → human name`` map for snapshot writes."""
    out: dict[int, str] = {}
    for m in masters:
        name = getattr(m, "name", "") or getattr(m, "title", "")
        out[int(m.id)] = str(name)
    return out


def build_service_lookup(services: list[Service]) -> dict[int, str]:
    """``id → title`` map for service display name lookup."""
    return {int(s.id): s.title for s in services}


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _extract_yc_id(comment: str) -> int | None:
    """Parse ``yclients_record_id=<digits>`` marker out of a free-text comment."""
    if not comment:
        return None
    marker = "yclients_record_id="
    idx = comment.find(marker)
    if idx < 0:
        return None
    rest = comment[idx + len(marker) :]
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
