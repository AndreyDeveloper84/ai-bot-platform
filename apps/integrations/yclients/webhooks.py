"""YClients admin-side webhook receiver (DRF-838 / Phase 1 / B2).

Ports ``mysite/maxbot/yclients_webhook.py``. The salon's admin
creates / edits / cancels a record in the YClients UI, YClients POSTs
the event to ``/api/v1/yclients/webhook/``, and we:

1. Resolve the tenant (single-tenant Phase 1 — see settings note below).
2. Look up the matching :class:`apps.identity.models.BotUser` by phone.
3. Persist a :class:`apps.booking.models.BookingRequest` row (snapshot
   of the booking) + two :class:`apps.booking.models.BookingReminder`
   rows (T-24h + T-2h) — these will fire via the B3 / B4 reminder
   pipeline.
4. Emit a ``booking_created_from_yclients_admin`` event so the message-
   send subsystem (separate ticket — see "scope cut" below) can
   dispatch a welcome message through whatever channel the BotUser
   is reachable on.

## Why ALWAYS 200

YClients' delivery loop retries any non-2xx response indefinitely.
A 5xx during a transient bug becomes a stream of duplicate deliveries
that mask the original problem and fill the audit log. Mysite learned
this in prod and the rule is non-negotiable: every failure mode here
writes an audit row and returns 200. Operators read the audit log
for delivery failures, never the HTTP status.

This is the same invariant the Sprint-10 catalog webhook
(:mod:`apps.catalog.webhooks.views`) enforces — they share the rule
because they share the upstream retry semantic (mysite Celery, also
upstream-controlled).

## Idempotency

YClients re-delivers on connection blips. Duplicate-safety relies on
``BookingReminder.unique_together(yclients_record_id, kind)`` — every
write goes through ``update_or_create`` keyed on that pair, so a
re-delivered ``record.create`` rewrites the same two reminder rows
instead of doubling the schedule. BookingRequest dedup is keyed off
the same yclients id (stored in :attr:`BookingRequest.comment`) — if
a request row already exists for the id, we don't create another.

## HMAC

The mysite source did NOT verify HMAC (YClients' public API didn't
support signed webhooks as of the port). We carry that forward — no
signature verification here. If YClients adds HMAC in a future API
version this is the first place to land it (and Sprint 10 / C3's
``_verify_signature`` is the template).

## Tenant resolution

YClients does not send our ``X-Tenant`` header. Phase 1 single-tenant
deployments configure ``settings.YCLIENTS_WEBHOOK_TENANT_SLUG``; the
receiver enters that tenant's :func:`apps.tenancy.context.tenant_scope`
for the duration of the request so ORM queries (``BotUser.objects``,
``BookingRequest.objects``) hit the right tenant.

Phase 2 will add a ``(yclients_company_id → tenant_slug)`` map on the
Tenant model and look up by the inbound payload's ``company_id`` —
the resolution point is centralised in :func:`_resolve_tenant` so the
upgrade is a one-line swap.

## Scope cuts (deferred follow-ups)

* **Welcome message dispatch** — mysite calls ``send_max_message``
  directly. The platform's channel-agnostic outbound layer lives in
  ``apps.channels.<channel>.outbound`` and wiring it in here would
  cross too many module boundaries for one PR. We emit a
  ``booking_created_from_yclients_admin`` event with the rendered text
  in ``properties.message``; a follow-up ticket will subscribe and
  dispatch.
* **Reschedule / cancel notification messages** — same as above; the
  event payload carries the rendered text, the subscriber will send.
* **Master / Service catalog linkage** — for the snapshot fields we
  use the names exactly as YClients sends them. The catalog mirror
  (Sprint 10 / C-track) hosts authoritative names; if we wanted to
  use *those* we'd need a name-resolution lookup that pulls
  ``apps.catalog`` into this module. Snapshot-at-write time matches
  mysite's behaviour and is correct for the audit-trail use case.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.audit.services import write_audit
from apps.booking.models import BookingReminder, BookingRequest
from apps.bookings.reminders_factory import create_reminders_for_booking
from apps.events.services import emit
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# ─── constants ────────────────────────────────────────────────────────────


_EVENT_BOOKING_CREATED_FROM_YCLIENTS_ADMIN = "booking_created_from_yclients_admin"


# Always-200 ack. JSON body returned regardless of internal outcome.
def _ack(status: str, **extra: object) -> JsonResponse:
    body: dict[str, object] = {"status": status, **extra}
    return JsonResponse(body, status=200)


# ─── view ─────────────────────────────────────────────────────────────────


@csrf_exempt
@require_POST
def yclients_webhook(request: HttpRequest) -> JsonResponse:
    """POST ``/api/v1/yclients/webhook/`` — YClients admin event receiver.

    Always returns 200 — see module docstring "Why ALWAYS 200".

    Failure modes (each writes an audit row, returns 200):

    * Unparseable JSON body / wrong content-type
    * Top-level non-dict payload
    * Tenant slug not configured in settings (or row missing)
    * Unhandled exception in the per-status handler

    Success: writes an audit row + ``booking_created_from_yclients_admin``
    event on ``record.create``; persists BookingRequest +
    BookingReminders. ``record.update`` / ``record.delete`` mutate
    existing reminder rows.
    """
    # We wrap EVERYTHING in a try/except to honour the always-200 rule.
    # Any uncaught exception inside the handler is an audit-row + 200,
    # never a 5xx.
    try:
        return _dispatch(request)
    except Exception as exc:  # noqa: BLE001 — see "Why ALWAYS 200"
        logger.exception("yclients_webhook: unhandled exception: %s", exc)
        write_audit(
            action="yclients.webhook.handler_exception",
            target="yclients.webhook",
            payload={"exception_type": type(exc).__name__},
        )
        return _ack("error", reason="handler_exception")


def _dispatch(request: HttpRequest) -> JsonResponse:
    """Parse → resolve tenant → branch on status. Each leaf returns 200."""
    payload = _parse_body(request)
    if payload is None:
        write_audit(
            action="yclients.webhook.rejected",
            target="yclients.webhook",
            payload={"reason": "malformed_json"},
        )
        return _ack("error", reason="malformed_json")

    resource = str(payload.get("resource") or "")
    if resource != "record":
        # Non-record events (clients, etc) — ignore quietly.
        return _ack("ignored", reason=f"resource={resource}")

    tenant = _resolve_tenant(payload)
    if tenant is None:
        write_audit(
            action="yclients.webhook.rejected",
            target="yclients.webhook",
            payload={
                "reason": "tenant_unresolved",
                "configured_slug": getattr(
                    settings,
                    "YCLIENTS_WEBHOOK_TENANT_SLUG",
                    "",
                ),
            },
        )
        return _ack("error", reason="tenant_unresolved")

    status = str(payload.get("status") or "")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        write_audit(
            action="yclients.webhook.rejected",
            target="yclients.webhook",
            payload={"reason": "data_not_dict"},
        )
        return _ack("error", reason="data_not_dict")

    with tenant_scope(tenant):
        if status == "create":
            result = _handle_create(tenant, data)
        elif status == "update":
            result = _handle_update(tenant, data)
        elif status == "delete":
            result = _handle_delete(tenant, data)
        else:
            result = {"status": "ignored", "reason": f"unknown_status={status}"}

    return JsonResponse(result, status=200)


# ─── tenant resolution ────────────────────────────────────────────────────


def _resolve_tenant(payload: dict[str, Any]) -> Tenant | None:
    """Phase 1: a single tenant from settings. See module docstring.

    Phase 2 hook: read ``payload['company_id']``, look up a mapping
    table on Tenant, fall through to the settings slug for the
    legacy single-tenant deployment. Centralised here for that swap.
    """
    slug = getattr(settings, "YCLIENTS_WEBHOOK_TENANT_SLUG", "") or ""
    if not slug:
        logger.warning(
            "yclients_webhook: YCLIENTS_WEBHOOK_TENANT_SLUG not set — receiver is dormant",
        )
        return None
    return Tenant.all_objects.filter(slug=slug, is_active=True).first()


# ─── body parsing ─────────────────────────────────────────────────────────


def _parse_body(request: HttpRequest) -> dict[str, Any] | None:
    """Return parsed JSON dict or None on any parse / type / encoding error.

    We don't gate on Content-Type — YClients has historically sent
    bodies as ``application/x-www-form-urlencoded`` with a JSON string
    inside on some panel versions. Just try to parse the bytes as
    JSON; if that fails we audit-200 anyway. The `request.body` access
    is what gets the raw bytes regardless of Content-Type.
    """
    try:
        parsed = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ─── phone normalisation ──────────────────────────────────────────────────


def normalize_phone(phone: str) -> str:
    """Digits-only canonical form.

    Russian phone variants in the wild: ``+7 999 123-45-67``,
    ``8(999)123-45-67``, ``79991234567``, raw ``9991234567``. Mysite's
    rule (verbatim port):

    * Strip everything that isn't a digit.
    * 11 digits starting with 7 or 8 → ``7XXXXXXXXXX``.
    * 10 digits → ``7XXXXXXXXXX``.
    * Anything else → return as-is (caller decides if usable).
    """
    if not phone:
        return ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits


def _phone_matches(stored: str, incoming: str) -> bool:
    """Last-10-digits comparison — handles mixed-format stored values."""
    a = normalize_phone(stored)
    b = normalize_phone(incoming)
    if not a or not b:
        return False
    return a.endswith(b[-10:])


def _find_bot_user(phone: str) -> BotUser | None:
    """Find a BotUser whose normalised phone matches the inbound number.

    BotUser.phone is supposed to be E.164-normalised at write time
    (Sprint 2 / D1 normalisation policy), but in practice we see raw
    values from older migrations. The double-check loop covers both
    shapes without scanning the whole table — index is on ``phone``,
    the ``contains last10`` filter is cheap.
    """
    if not phone:
        return None
    digits = normalize_phone(phone)
    if not digits:
        return None
    last10 = digits[-10:]
    # Index-friendly fast path first.
    candidate = BotUser.objects.filter(phone__contains=last10).first()
    if candidate is not None:
        return candidate
    # Slow path: scan rows with non-empty phone (still tenant-scoped).
    for bu in BotUser.objects.exclude(phone="").only("id", "phone", "chat_id"):
        if _phone_matches(bu.phone, phone):
            return bu
    return None


# ─── datetime helpers ─────────────────────────────────────────────────────


def _parse_yclients_datetime(s: str) -> datetime | None:
    """Parse YClients' ``YYYY-MM-DD HH:MM:SS`` (naive Moscow) → aware UTC-equivalent.

    Returns ``None`` on any parse error so callers can audit-200.
    Mysite treats the naive value as Moscow local; we mirror that —
    salon TZ assumption matches reality (no multi-region clients yet).
    """
    if not s:
        return None
    iso = s.replace(" ", "T")
    try:
        naive = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    if naive.tzinfo is None:
        # Make it tz-aware in Django's current timezone (Moscow per settings).
        return timezone.make_aware(naive)
    return naive


def _format_dt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%d.%m at %H:%M")


# ─── per-status handlers ──────────────────────────────────────────────────


def _handle_create(tenant: Tenant, data: dict[str, Any]) -> dict[str, Any]:
    """``record.create`` — write BookingRequest + 2 reminders + emit event.

    Idempotent: re-delivery hits the same ``update_or_create`` calls
    on (yclients_record_id, kind) and rewrites the same rows.
    """
    yc_id = str(data.get("id") or "").strip()
    if not yc_id:
        return {"status": "skipped", "reason": "no_record_id"}

    client = data.get("client") or {}
    phone = str(client.get("phone") or "")
    bot_user = _find_bot_user(phone)
    if bot_user is None:
        # Not a BotUser we know about — audit row, no booking write.
        # YClients keeps the record on their side; we just can't link
        # it to a chat conversation.
        write_audit(
            action="yclients.webhook.no_bot_user_match",
            target="yclients.webhook",
            payload={
                "yclients_record_id": yc_id,
                # Phone is PII — log only last 4 digits.
                "phone_tail": normalize_phone(phone)[-4:],
            },
        )
        return {"status": "skipped", "reason": "no_bot_user_match"}

    if not bot_user.chat_id:
        write_audit(
            action="yclients.webhook.bot_user_no_chat_id",
            target="yclients.webhook",
            payload={
                "yclients_record_id": yc_id,
                "bot_user_id": str(bot_user.id),
            },
        )
        return {"status": "skipped", "reason": "bot_user_no_chat_id"}

    visit_at = _parse_yclients_datetime(str(data.get("datetime") or ""))
    if visit_at is None:
        write_audit(
            action="yclients.webhook.no_datetime",
            target="yclients.webhook",
            payload={"yclients_record_id": yc_id},
        )
        return {"status": "skipped", "reason": "no_datetime"}

    staff = data.get("staff") or {}
    services = data.get("services") or []
    master_name = str(staff.get("name") or "")
    service_name = (
        str(services[0].get("title")) if services and isinstance(services[0], dict) else ""
    )

    # ── BookingRequest: idempotent — keyed on yc_id in `comment`.
    # We use a stable marker substring so re-delivery doesn't double
    # the request rows. BookingRequest doesn't have a yclients_id
    # column (snapshot-only model); the comment field is the contract
    # surface mysite chose and we preserve it for cross-system join
    # queries. ``get_or_create`` doesn't accept ``__contains`` lookups
    # in its constraint kwargs cleanly, so we do an explicit lookup
    # then create — both run inside the same request which YClients
    # serialises per record (single retry loop), so the race window
    # is small and the audit log catches the duplicate if it ever
    # happens.
    # Skills retro hotfix #1 (cont'd): anchor on the pipe-space prefix so
    # a free-text comment fragment can't spoof the idempotency lookup.
    # Both writers emit ``"<source> | yclients_record_id=<id>"`` — the
    # pipe character is reserved in our comment format and not injectable
    # through normal user-input paths.
    yc_marker = f" | yclients_record_id={yc_id}"
    booking = BookingRequest.objects.filter(
        bot_user=bot_user,
        comment__contains=yc_marker,
    ).first()
    if booking is None:
        booking = BookingRequest.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            category_name="",
            service_name=service_name or "—",
            master_name=master_name or "—",
            client_name=(str(client.get("name") or "") or bot_user.client_name or "Client"),
            client_phone=phone or bot_user.phone or "",
            comment=f"YClients admin booking | {yc_marker}",
            source="yclients_admin",
            is_processed=True,
        )

    # ── BookingReminders: idempotent via unique_together. R1 (DRF-844)
    # routes both writes through the factory so reminder scheduling
    # rules live in exactly one place (see
    # ``apps.bookings.reminders_factory`` module docstring). The
    # factory writes T-24h then T-2h with the same defaults as the
    # original inline code; behaviour is byte-equivalent.
    create_reminders_for_booking(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        visit_at=visit_at,
        master_name=master_name,
        service_name=service_name,
        booking_request=booking,
    )

    # ── Welcome event (scope cut — see module docstring).
    greeting_name = bot_user.client_name or bot_user.display_name or ""
    salute = f"{greeting_name}, " if greeting_name else ""
    message_text = (
        f"{salute}you are booked for {_format_dt(visit_at)}\n"
        f"Master: {master_name or '—'}\n"
        f"Service: {service_name or '—'}\n\n"
        f"We will remind you the day before."
    )
    # transaction.on_commit so a rollback (e.g. another row constraint
    # fires below) doesn't push a notification for a booking that
    # didn't persist. Same rule as the rest of the codebase — see
    # apps/identity/services.py and apps/consent/decorators.py.
    transaction.on_commit(
        lambda: emit(
            _EVENT_BOOKING_CREATED_FROM_YCLIENTS_ADMIN,
            properties={
                "yclients_record_id": yc_id,
                "booking_request_id": str(booking.id),
                "bot_user_id": str(bot_user.id),
                "chat_id": bot_user.chat_id,
                "channel": bot_user.channel,
                "visit_at": visit_at.isoformat(),
                "master_name": master_name,
                "service_name": service_name,
                "message": message_text,
            },
            distinct_id=str(bot_user.id),
        )
    )
    write_audit(
        action="yclients.webhook.create_processed",
        target="yclients.webhook",
        target_id=booking.id,
        payload={
            "yclients_record_id": yc_id,
            "bot_user_id": str(bot_user.id),
        },
    )
    logger.info(
        "yclients_webhook.create: yc=%s booking=%s bot_user=%s",
        yc_id,
        booking.id,
        bot_user.id,
    )
    return {
        "status": "created",
        "yclients_record_id": yc_id,
        "booking_request_id": str(booking.id),
    }


def _handle_update(tenant: Tenant, data: dict[str, Any]) -> dict[str, Any]:
    """``record.update`` — recompute reminders for the new datetime.

    Mysite's semantic: the OLD reminder rows are CANCELLED and NEW
    rows take their place (different visit_at, scheduling +
    chat_id snapshot fresh). We use update_or_create on the unique
    pair so re-delivery is idempotent — the row gets the new
    visit_at and stays PENDING.

    If no reminders exist yet (we missed the create event), fall
    through to ``_handle_create`` — mysite does the same.
    """
    yc_id = str(data.get("id") or "").strip()
    if not yc_id:
        return {"status": "skipped", "reason": "no_record_id"}

    new_visit_at = _parse_yclients_datetime(str(data.get("datetime") or ""))
    if new_visit_at is None:
        write_audit(
            action="yclients.webhook.no_datetime",
            target="yclients.webhook",
            payload={"yclients_record_id": yc_id, "event": "update"},
        )
        return {"status": "skipped", "reason": "no_datetime"}

    existing = BookingReminder.objects.filter(yclients_record_id=yc_id)
    if not existing.exists():
        return _handle_create(tenant, data)

    # Snapshot for the audit row (visit_at + bot_user) BEFORE we
    # rewrite the reminders.
    sample = existing.select_related("bot_user").first()
    if sample is None or sample.bot_user is None:
        # Defensive — existence check above guarantees sample, and the
        # BookingReminder.bot_user FK is non-null (CASCADE) so the row
        # can't have a NULL bot_user_id. But mypy can't see that and a
        # belt-and-braces fall-through to create matches mysite's
        # "missed-event" recovery semantic.
        return _handle_create(tenant, data)
    old_visit_at = sample.visit_at
    bot_user = sample.bot_user

    staff = data.get("staff") or {}
    services = data.get("services") or []
    master_name = str(staff.get("name") or sample.master_name)
    service_name = (
        str(services[0].get("title"))
        if services and isinstance(services[0], dict)
        else sample.service_name
    )

    # Mark old rows CANCELLED then re-schedule via the factory.
    # Why CANCEL the existing rows before re-creating:
    # the dispatcher reads (status=PENDING, scheduled_at<now) and a
    # naive in-place mutation of scheduled_at while sent_at is set
    # would let the dispatcher skip the now-rescheduled reminder.
    # The factory's update_or_create on (yc_id, kind) flips status
    # back to PENDING in defaults, so the explicit pre-cancel is
    # belt-and-braces — the CANCELLED→PENDING transition in the
    # factory's defaults catches it either way. The explicit pre-
    # cancel is preserved as an audit-friendly trail (a reader
    # scanning the rows sees "old rows cancelled, new rows pending"
    # rather than a single bumped row).
    existing.update(status=BookingReminder.Status.CANCELLED)

    create_reminders_for_booking(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        visit_at=new_visit_at,
        master_name=master_name,
        service_name=service_name,
    )

    write_audit(
        action="yclients.webhook.update_processed",
        target="yclients.webhook",
        payload={
            "yclients_record_id": yc_id,
            "old_visit_at": old_visit_at.isoformat() if old_visit_at else None,
            "new_visit_at": new_visit_at.isoformat(),
        },
    )
    logger.info(
        "yclients_webhook.update: yc=%s new_visit_at=%s",
        yc_id,
        new_visit_at.isoformat(),
    )
    return {
        "status": "updated",
        "yclients_record_id": yc_id,
        "new_visit_at": new_visit_at.isoformat(),
    }


def _handle_delete(tenant: Tenant, data: dict[str, Any]) -> dict[str, Any]:
    """``record.delete`` — mark existing reminders CANCELLED.

    No hard delete: the audit-trail use case (cancellation analytics,
    "why didn't this reminder fire" investigations) needs the rows.
    Mysite uses the same soft-cancel approach.
    """
    yc_id = str(data.get("id") or "").strip()
    if not yc_id:
        return {"status": "skipped", "reason": "no_record_id"}

    affected = BookingReminder.objects.filter(yclients_record_id=yc_id).exclude(
        status=BookingReminder.Status.CANCELLED,
    )
    count = affected.update(status=BookingReminder.Status.CANCELLED)

    write_audit(
        action="yclients.webhook.delete_processed",
        target="yclients.webhook",
        payload={"yclients_record_id": yc_id, "cancelled": count},
    )
    logger.info(
        "yclients_webhook.delete: yc=%s cancelled=%d",
        yc_id,
        count,
    )
    return {
        "status": "deleted",
        "yclients_record_id": yc_id,
        "cancelled": count,
    }
