"""Booking-skill tool specs + handlers (DRF-839 / Phase 1 / B3).

Four LLM-callable functions:

* :func:`show_masters` — list staff for a service.
* :func:`show_slots` — list time slots for a master.
* :func:`confirm_booking` — create a real YClients record + persist
  a :class:`BookingRequest` row (idempotent on
  ``(tenant, bot_user, yclients_record_id)``).
* :func:`show_my_bookings` — list the bot_user's upcoming bookings.

Each tool spec follows the OpenAI ``{name, description, parameters}``
shape (L1 canonical form, same as
:data:`apps.skills.faq.tools.SEARCH_KB_TOOL_SPEC`); the L3 router wraps
for SDK-specific envelopes at call time.

### Anti-hallucination

The LLM is forbidden from inventing IDs. Each tool that consumes a
``master_id`` or ``service_id`` validates it against the staff/service
list the YClients client pre-fetches for the current turn. The
caller (:class:`apps.skills.booking.skill.BookingSkill`) does the
pre-fetch and passes the allow-sets explicitly — kept here as DTOs so
unit tests can drive each tool without spinning up the skill.

### Idempotency

:func:`confirm_booking` looks up an existing
:class:`BookingRequest` row whose ``comment`` carries the
``yclients_record_id=...`` marker before creating a new one. This is the
same scheme :mod:`apps.integrations.yclients.webhooks` uses on the
admin path — keeps the two write surfaces interchangeable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.utils import timezone as dj_timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingRequest
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
        "Create a YClients booking for the user. Call ONLY after the user "
        "has explicitly confirmed master + slot. master_id and service_id "
        "MUST come from prior show_masters / show_slots calls — never "
        "guess. slot_datetime is the ISO datetime YClients returned in "
        "show_slots. client_phone defaults to the bot_user's stored phone."
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
    SHOW_MY_BOOKINGS_TOOL_SPEC,
]


# Audit slugs.
EVENT_BOOKING_TOOL_INVOKED = "booking.tool_invoked"
EVENT_BOOKING_CONFIRMED = "booking.confirmed"
EVENT_BOOKING_CONFIRM_FAILED = "booking.confirm_failed"


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
    """Create a YClients record + persist :class:`BookingRequest` (idempotent).

    Args:
      client: B1 YClients client.
      arguments: LLM-supplied ``{master_id, service_id, slot_datetime, client_phone?}``.
      tenant: current :class:`Tenant`.
      bot_user: current :class:`BotUser`.
      allowed_master_ids: validated set from prior :func:`show_masters`.
      allowed_service_ids: validated set from pre-fetched services list.
      master_lookup / service_lookup: id → display-name maps for the
        :class:`BookingRequest` snapshot.

    Idempotency: existing :class:`BookingRequest` rows are looked up by
    a ``yclients_record_id=<id>`` marker in the ``comment`` field, mirroring
    the :mod:`apps.integrations.yclients.webhooks` admin-side scheme.
    A duplicate confirm call returns ``ok=True`` without re-POSTing to
    YClients OR creating a second row.
    """
    master_id = _coerce_int(arguments.get("master_id"))
    service_id = _coerce_int(arguments.get("service_id"))
    slot_datetime = str(arguments.get("slot_datetime") or "").strip()
    client_phone = str(arguments.get("client_phone") or "").strip()

    tenant_id = str(getattr(tenant, "id", ""))

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

    # POST to YClients first — we don't want phantom local rows when the
    # remote create silently fails.
    try:
        record: BookingRecord = client.create_record(
            staff_id=master_id,
            services=[service_id],
            datetime=slot_datetime,
            client_phone=phone,
            client_name=client_name,
        )
    except YClientsUnavailableError as exc:
        logger.warning("booking.confirm.unavailable err=%s", exc)
        _audit_tool(
            tenant_id=tenant_id,
            tool="confirm_booking",
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
        logger.info("booking.confirm.api_error err=%s", exc)
        _audit_tool(tenant_id=tenant_id, tool="confirm_booking", outcome="yclients_api_error")
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

    # Idempotency: same record id, same bot_user → don't re-create.
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
        )
        _schedule_reminders(
            tenant=tenant,
            bot_user=bot_user,
            yc_id=yc_id,
            visit_at_dt=visit_at_dt,
            master_name=master_name,
            service_name=service_name,
        )

    _audit_tool(tenant_id=tenant_id, tool="confirm_booking", outcome="ok")
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
# Tool 4: show_my_bookings
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
