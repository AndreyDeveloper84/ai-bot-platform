"""New-booking notification to MAX (DRF-1030).

Until this module landed, **nobody on the salon side learned about a
new booking.** The single channel was an Ayla-backend push into the
Ayla Pro mobile app: ``booking.created`` → outbox →
``handle_booking_created`` → template ``appointment_created_specialist``
with ``channel = PUSH``, ``app_type = "pro"``. On the pilot there are
zero active ``DeviceToken`` rows, and the backend dispatcher performs
no fallback for ``PUSH`` — over the whole pilot history 26 notifications
were produced, 20 ``failed``, 6 ``pending``, **0 ``sent``**. On the bot
side none of the seven ``booking.*`` handlers addressed the salon at
all.

This module closes that gap on the bot side, over the transport that
demonstrably works on the pilot: a plain MAX message, sent with the
very same fan-out primitive DRF-1029 uses for handoff escalations
(:func:`apps.handoff.notify.send_max_notification`). No new transport,
no new queue, no dependency on the mobile app.

### Addressing cascade (first hit wins)

1. **The master personally** — ``CatalogMaster.linked_bot_user.chat_id``
   for the appointment's specialist. On the pilot this yields nothing:
   ``linked_bot_user`` is NULL for all four masters and ``max_handle``
   is empty. Linking masters to their MAX accounts is a separate task;
   the branch is implemented here so that when the link appears, the
   message starts going to the right person with **no code change**.
2. **The salon manager** — ``Tenant.manager_chat_id``. The field exists
   and is already the escalation destination for stale reminders
   (DRF-845); on the pilot it is currently empty.
3. **The configured fallback channel** —
   ``HANDOFF_NOTIFY_MAX_CHAT_IDS``. Deliberately the *same* setting as
   DRF-1029 rather than a new one: on the pilot it already holds the
   owner's chat, so the booking notification reaches a human on day one
   without an env change. If a salon later wants booking alerts split
   from escalation alerts, that is a settings-level split, not a
   rewrite of this module.
4. **Nobody** — an explicit WARNING log line. Silence was the old
   behaviour and it is exactly what made the gap invisible for months;
   a booking that could not be announced must leave a trace.

### Contract (mirrors DRF-1029 §3 — do not weaken)

* **After commit, never inside the transaction.** The consumer
  registers :func:`notify_booking_created` through
  ``transaction.on_commit``; a rolled-back ``booking.created`` must
  never announce a booking that does not exist.
* **Best-effort, hard.** Nothing here may break event ingestion. A
  failure would turn a *missing notification* into a *dead-lettered
  booking event* — strictly worse than the status quo. Everything is
  caught and logged.
* **Never block the consumer.** Sends are synchronous with the short
  DRF-1029 timeout and no retries; the ingest path is latency-critical
  (DRF-989).
* **No client PII.** Per the owner's decision recorded in DRF-1039 the
  client's identity and contact are NOT passed to the performer. The
  message carries service, master, time, source and the appointment id
  — nothing about the client, not even a name.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q

from apps.catalog.models import CatalogMaster, CatalogService
from apps.handoff.notify import get_notify_chat_ids, send_max_notification
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Placeholder for any field the catalog mirror cannot resolve. The
# message is still worth sending without a service name — the time and
# the master usually identify the slot for the salon.
_UNKNOWN = "—"

_DEFAULT_TZ = "Europe/Moscow"

# ``RemoteBookingProxy.Source`` values (event-contract.md §3.1), plus a
# few strings the pilot producer emits. Unknown values pass through
# verbatim rather than being hidden — an unrecognised source is
# information, not noise.
_SOURCE_LABELS: dict[str, str] = {
    "mobile_app": "мобильное приложение",
    "admin_console": "админка",
    "automation": "автоматизация",
    "yclients_sync": "синхронизация YClients",
    "ayla_bot": "бот Ayla",
    "bot": "бот Ayla",
    "miniapp": "мини-приложение",
}


@dataclass(frozen=True)
class NotifyTarget:
    """Resolved recipients plus the cascade step that produced them.

    ``channel`` is one of ``master`` / ``manager`` / ``fallback`` /
    ``none`` and exists so logs (and tests) can assert *which* rung of
    the cascade answered, not merely that something was sent.
    """

    chat_ids: tuple[str, ...]
    channel: str


def _clean_chat_id(value: object) -> str:
    return str(value or "").strip()


def resolve_master(*, tenant: Tenant, specialist_id: UUID | None) -> CatalogMaster | None:
    """Catalog-mirror row for the appointment's specialist, if mirrored.

    Matched on **either** mirror key. The masters mirror is keyed on the
    Ayla ``SpecialistProfile.id`` (``CatalogMaster.id`` — see
    ``upsert_specialists``) and separately carries the specialist's Ayla
    ``User.id`` in ``ayla_user_id``. ``booking.created.specialist_id`` is
    documented only as «the master assigned to the appointment»
    (event-contract.md §3.1), which does not pin down which of the two
    the producer sends. Accepting both costs one indexed OR and removes
    the failure mode where an unresolved id silently degrades the message
    to «Мастер: —».

    The ``on_commit`` callback runs outside any tenant context, so the
    read establishes its own ``tenant_scope`` rather than reaching for
    the cross-tenant ``all_tenants`` manager (MKT1 — cross-tenant
    catalog reads belong to ``apps/marketplace`` alone). The explicit
    ``tenant=`` filter on top of the scope is the same belt-and-braces
    the catalog upserter uses: correct under every
    ``STRICT_TENANT_SCOPE`` mode, including the pilot's audit mode where
    a missing scope returns emptiness instead of raising.
    """

    if specialist_id is None:
        return None
    with tenant_scope(tenant):
        return (
            CatalogMaster.objects.filter(tenant=tenant)
            .filter(Q(id=specialist_id) | Q(ayla_user_id=specialist_id))
            .select_related("linked_bot_user")
            .first()
        )


def resolve_service_name(*, tenant: Tenant, service_id: UUID | None) -> str:
    """Display name for the booked service, or ``—`` when unmirrored."""

    if service_id is None:
        return _UNKNOWN
    with tenant_scope(tenant):
        row = (
            CatalogService.objects.filter(tenant=tenant, ayla_service_id=service_id)
            .only("name")
            .first()
        )
    return (row.name if row else "").strip() or _UNKNOWN


def resolve_target(*, tenant: Tenant, master: CatalogMaster | None) -> NotifyTarget:
    """Walk the addressing cascade; the first rung with an address wins.

    Personal delivery to the master is intentionally *exclusive*: once
    a master is reachable in MAX, copying every booking to the manager
    and the owner's chat as well would train everyone to ignore the
    channel. Escalation to a wider audience belongs to a follow-up on
    unacknowledged bookings, not to the creation event.
    """

    linked = getattr(master, "linked_bot_user", None) if master is not None else None
    master_chat_id = _clean_chat_id(getattr(linked, "chat_id", ""))
    if master_chat_id:
        return NotifyTarget(chat_ids=(master_chat_id,), channel="master")

    manager_chat_id = _clean_chat_id(getattr(tenant, "manager_chat_id", ""))
    if manager_chat_id:
        return NotifyTarget(chat_ids=(manager_chat_id,), channel="manager")

    fallback = tuple(c for c in (_clean_chat_id(c) for c in get_notify_chat_ids()) if c)
    if fallback:
        return NotifyTarget(chat_ids=fallback, channel="fallback")

    return NotifyTarget(chat_ids=(), channel="none")


def _tenant_tz(tenant: Tenant) -> ZoneInfo:
    """Tenant-local timezone, falling back to MSK then UTC.

    A booking rendered in the wrong timezone is worse than no message:
    the salon would prepare for the wrong hour. An invalid tenant value
    therefore degrades to the pilot's real timezone rather than to UTC.
    """

    for candidate in (getattr(tenant, "timezone", "") or "", _DEFAULT_TZ):
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def source_label(raw_source: str) -> str:
    """Human-readable booking source; unknown values pass through."""

    cleaned = (raw_source or "").strip()
    if not cleaned:
        return _UNKNOWN
    return _SOURCE_LABELS.get(cleaned, cleaned)


def build_booking_created_notification(
    *,
    tenant: Tenant,
    appointment_id: UUID,
    start_at: dt.datetime,
    service_name: str,
    master_name: str,
    raw_source: str,
) -> str:
    """Format the salon-facing text for a fresh booking.

    NO client data of any kind — see the DRF-1039 note in the module
    docstring. The appointment id is included so the salon can quote a
    single row to support; it is an opaque Ayla UUID, not client PII.
    """

    when = start_at.astimezone(_tenant_tz(tenant)).strftime("%d.%m.%Y в %H:%M")
    lines = [
        "🆕 Новая запись",
        f"Салон: {tenant.name}",
        f"Услуга: {service_name or _UNKNOWN}",
        f"Мастер: {master_name or _UNKNOWN}",
        f"Когда: {when}",
        f"Источник: {source_label(raw_source)}",
        f"Запись: {appointment_id}",
    ]
    return "\n".join(lines)


def notify_booking_created(
    *,
    tenant: Tenant,
    appointment_id: UUID,
    start_at: dt.datetime,
    specialist_id: UUID | None,
    service_id: UUID | None,
    raw_source: str,
) -> None:
    """``on_commit`` entry point for ``booking.created``. NEVER raises.

    Register through :func:`schedule_booking_created_notification`; do
    not call directly from inside the ingest transaction.
    """

    try:
        master = resolve_master(tenant=tenant, specialist_id=specialist_id)
        target = resolve_target(tenant=tenant, master=master)
        if not target.chat_ids:
            # The whole point of DRF-1030: an unannounceable booking
            # must be loud. Every rung of the cascade being empty is a
            # configuration defect, not a normal state.
            logger.warning(
                "booking.notify.no_recipients tenant=%s appointment_id=%s "
                "specialist_id=%s — no linked master chat, no manager_chat_id, "
                "no HANDOFF_NOTIFY_MAX_CHAT_IDS: nobody was told about this booking",
                tenant.slug,
                appointment_id,
                specialist_id,
            )
            return

        text = build_booking_created_notification(
            tenant=tenant,
            appointment_id=appointment_id,
            start_at=start_at,
            service_name=resolve_service_name(tenant=tenant, service_id=service_id),
            master_name=(getattr(master, "name", "") or "").strip() or _UNKNOWN,
            raw_source=raw_source,
        )

        failures = send_max_notification(text=text, chat_ids=target.chat_ids)
        if failures == 0:
            logger.info(
                "booking.notify.sent tenant=%s appointment_id=%s channel=%s recipients=%d",
                tenant.slug,
                appointment_id,
                target.channel,
                len(target.chat_ids),
            )
        else:
            logger.warning(
                "booking.notify.partial_failure tenant=%s appointment_id=%s "
                "channel=%s recipients=%d failures=%d",
                tenant.slug,
                appointment_id,
                target.channel,
                len(target.chat_ids),
                failures,
            )
    except Exception:  # noqa: BLE001 — hard containment; ingest must not break
        logger.exception(
            "booking.notify.unexpected appointment_id=%s",
            appointment_id,
        )


def schedule_booking_created_notification(
    *,
    tenant: Tenant,
    appointment_id: UUID,
    start_at: dt.datetime,
    specialist_id: UUID | None,
    service_id: UUID | None,
    raw_source: str,
) -> None:
    """Queue the notification for after the ingest transaction commits.

    Called from ``handle_booking_created``. Every catalog read and every
    network call happens in the callback, so a re-delivered or
    rolled-back event costs the handler nothing.
    """

    transaction.on_commit(
        lambda: notify_booking_created(
            tenant=tenant,
            appointment_id=appointment_id,
            start_at=start_at,
            specialist_id=specialist_id,
            service_id=service_id,
            raw_source=raw_source,
        )
    )
