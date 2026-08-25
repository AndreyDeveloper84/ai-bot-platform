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

### Delivery fan-out

* **The specialist personally** — when the appointment's master has a
  linked MAX account (``CatalogMaster.linked_bot_user.chat_id``), he
  receives his own copy, addressed to him («У вас новая запись»). The
  epic's contract is «if the master does not learn, the visit does not
  happen», so the specialist is an *additional* recipient, not an
  alternative to the salon (601564a delivered master-OR-salon
  exclusively; the follow-up decision recorded here is master-AND-salon).
  On the pilot every master is unlinked today; linking is a data
  change, not a code change.
* **An unreachable specialist is visible** — a mirrored master without
  a linked account, or a specialist with no mirror row at all, leaves a
  WARNING log line and a ``booking.specialist_unreachable`` audit row.
  The push era hid exactly this state behind a quiet ``failed`` in the
  database; it must never be silent again.
* **The salon cascade (first hit wins)** — ``Tenant.manager_chat_id``,
  then ``HANDOFF_NOTIFY_MAX_CHAT_IDS``. Deliberately the *same* setting
  as DRF-1029 rather than a new one: on the pilot it already holds the
  owner's chat, so the booking notification reaches a human on day one
  without an env change. If a salon later wants booking alerts split
  from escalation alerts, that is a settings-level split, not a
  rewrite of this module.
* **Nobody at all** — an explicit WARNING log line. Silence was the old
  behaviour and it is exactly what made the gap invisible for months;
  a booking that could not be announced must leave a trace.

### Contract (mirrors DRF-1029 §3 — do not weaken)

* **After commit, never inside the transaction.** The consumer
  registers :func:`notify_booking_created` through
  ``transaction.on_commit``; a rolled-back ``booking.created`` must
  never announce a booking that does not exist.
* **Once per appointment — decided by the caller, not here.** This
  module sends whatever it is handed; the «only once» guarantee is the
  consumer's per-appointment claim on
  ``RemoteBookingProxy.salon_notified_at``
  (``apps.eventbus.consumers.booking._claim_announcement``). Until
  DRF-1069 that gate was «we inserted the mirror row», which was false
  for every booking made in the bot's own dialog — and so the salon
  heard about none of them. Any new call site must take the claim too.
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
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q

from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster, CatalogService
from apps.channels.bot_context import bot_scope
from apps.handoff.notify import get_notify_chat_ids, send_max_notification
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)

#: Stream of the staff-facing bot. Kept as a literal rather than imported
#: from the handler to avoid apps.booking depending on apps.channels.max.
SALON_STREAM = "max_salon"


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

# Source value the consumer substitutes when local state proves the
# booking was made in the bot's own dialog (DRF-1069).
#
# The event cannot tell us this: the bot does not pass a ``source``
# through ``apps.skills.booking.provider.create_record``, and
# ``RemoteBookingProxy.Source`` carries no bot value at all — the mirror
# row the dialog path writes labels itself ``automation``. Since the
# conversational booking is the product's main path and DRF-1069 is what
# first brings those bookings to the salon at all, «Источник:
# автоматизация» would be the salon's introduction to them. It is a
# label, nothing branches on it.
CHAT_ORIGIN_SOURCE: Final[str] = "ayla_bot"


@dataclass(frozen=True)
class NotifyTarget:
    """Resolved salon recipients plus the cascade step that produced them.

    ``channel`` is one of ``manager`` / ``fallback`` / ``none`` and
    exists so logs (and tests) can assert *which* rung of the cascade
    answered, not merely that something was sent. The specialist's
    personal address is resolved separately — see
    :func:`resolve_specialist_chat_id` — because it is an additional
    recipient, not a rung of this cascade.
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


def resolve_specialist_chat_id(master: CatalogMaster | None) -> str:
    """The specialist's personal MAX chat_id, or ``""`` when unreachable.

    «Reachable» means a *linked account*: ``linked_bot_user`` is set by
    the staff-invite accept flow and by solo onboarding, and the BotUser
    carries the chat_id of the master's own dialog with the bot. On the
    pilot every master is unlinked today — linking them is a data
    change, not a code change, and this resolver starts answering the
    moment it happens.
    """

    linked = getattr(master, "linked_bot_user", None) if master is not None else None
    return _clean_chat_id(getattr(linked, "chat_id", ""))


def resolve_salon_target(*, tenant: Tenant) -> NotifyTarget:
    """Walk the salon-side cascade; the first rung with an address wins.

    The salon rungs stay exclusive among themselves (a manager who
    receives every booking does not also need the fallback copy); only
    the specialist's personal copy is additive — see the module
    docstring.
    """

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


def build_specialist_booking_notification(
    *,
    tenant: Tenant,
    appointment_id: UUID,
    start_at: dt.datetime,
    service_name: str,
    raw_source: str,
) -> str:
    """Format the specialist's personal copy of the announcement.

    Addressed to the performer himself — «У вас новая запись», and no
    «Мастер:» line: telling Тихонова Ольга that the master is Тихонова
    Ольга only adds noise. Same DRF-1039 rule as the salon copy: NO
    client data of any kind.
    """

    when = start_at.astimezone(_tenant_tz(tenant)).strftime("%d.%m.%Y в %H:%M")
    lines = [
        "🆕 У вас новая запись",
        f"Салон: {tenant.name}",
        f"Услуга: {service_name or _UNKNOWN}",
        f"Когда: {when}",
        f"Источник: {source_label(raw_source)}",
        f"Запись: {appointment_id}",
    ]
    return "\n".join(lines)


def _salon_bot_for(tenant: Tenant):
    """The salon's staff bot, or ``None`` when it has none.

    ``None`` means outbound keeps using the single configured token, i.e.
    exactly the behaviour before DRF-1061 — see the call site for why that
    is the right fallback for THIS message specifically.
    """

    try:
        from apps.channels.bot_registry import effective_registry, resolve_by_tenant_stream

        return resolve_by_tenant_stream(tenant.slug, SALON_STREAM, effective_registry())
    except Exception:  # noqa: BLE001 — identity must never break ingest
        logger.warning("booking.notify.registry_unavailable tenant=%s", tenant.slug)
        return None


def _audit_specialist_unreachable(
    *,
    tenant: Tenant,
    appointment_id: UUID,
    specialist_id: UUID,
    master: CatalogMaster | None,
    reason: str,
) -> None:
    """Audit-row the unreachable specialist so the gap survives log rotation.

    Best-effort in the same sense as the send itself: a broken audit
    must never cost the salon its message (write_audit already swallows
    its own errors; the wrap here covers everything around it).
    """

    try:
        with tenant_scope(tenant):
            write_audit(
                "booking.specialist_unreachable",
                target="RemoteBookingProxy",
                target_id=appointment_id,
                payload={
                    "tenant": tenant.slug,
                    "specialist_id": str(specialist_id),
                    "master_id": str(master.id) if master is not None else None,
                    "reason": reason,
                },
            )
    except Exception:  # noqa: BLE001 — audit is observational, never breaks the fan-out
        logger.exception(
            "booking.notify.specialist_unreachable_audit_failed appointment_id=%s",
            appointment_id,
        )


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
        specialist_chat_id = resolve_specialist_chat_id(master)
        service_name = resolve_service_name(tenant=tenant, service_id=service_id)
        specialist_notified = False

        # Send as the SALON bot when the salon has one (DRF-1061).
        #
        # This message is work: "you have a new booking". Arriving from the
        # customer-facing avatar it reads as a marketing push to the very
        # people who are supposed to act on it, and a reply to it lands in
        # the customer funnel. The staff bot exists precisely to keep those
        # two conversations apart.
        #
        # bot_scope(None) is NOT neutral — outbound falls back to
        # settings.MAX_BOT_TOKEN — so when no staff bot is configured we
        # deliberately keep today's behaviour rather than inventing one:
        # a notification from the client bot is worse than nothing only in
        # tone, whereas silence is worse in substance. That is the one case
        # where the wrong avatar beats no message.
        with bot_scope(_salon_bot_for(tenant)):
            if specialist_chat_id:
                # The specialist goes FIRST: if MAX dies mid-fan-out, the
                # epic's priority recipient already has the message.
                personal = build_specialist_booking_notification(
                    tenant=tenant,
                    appointment_id=appointment_id,
                    start_at=start_at,
                    service_name=service_name,
                    raw_source=raw_source,
                )
                failures = send_max_notification(text=personal, chat_ids=(specialist_chat_id,))
                specialist_notified = failures == 0
                if specialist_notified:
                    logger.info(
                        "booking.notify.sent tenant=%s appointment_id=%s "
                        "channel=master recipients=1",
                        tenant.slug,
                        appointment_id,
                    )
                else:
                    logger.warning(
                        "booking.notify.partial_failure tenant=%s appointment_id=%s "
                        "channel=master recipients=1 failures=%d",
                        tenant.slug,
                        appointment_id,
                        failures,
                    )
            elif specialist_id is not None:
                # The whole point of this ticket: a specialist who cannot
                # be reached must be VISIBLE. The push era recorded this
                # exact state as a quiet ``failed`` row and the pilot ran
                # on it for months — here it is a WARNING plus an audit
                # row, and the salon cascade below still runs.
                reason = "no_mirror_row" if master is None else "no_linked_chat"
                logger.warning(
                    "booking.notify.specialist_unreachable tenant=%s appointment_id=%s "
                    "specialist_id=%s reason=%s — the master has no reachable MAX "
                    "address; the salon is told instead",
                    tenant.slug,
                    appointment_id,
                    specialist_id,
                    reason,
                )
                _audit_specialist_unreachable(
                    tenant=tenant,
                    appointment_id=appointment_id,
                    specialist_id=specialist_id,
                    master=master,
                    reason=reason,
                )

            target = resolve_salon_target(tenant=tenant)
            if target.chat_ids:
                text = build_booking_created_notification(
                    tenant=tenant,
                    appointment_id=appointment_id,
                    start_at=start_at,
                    service_name=service_name,
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
            elif not specialist_notified:
                # An unannounceable booking must be loud. Every address
                # being empty is a configuration defect, not a normal
                # state. (When the specialist WAS notified the booking is
                # announced — a missing salon address is then a quieter
                # observation, already covered by the cascade semantics.)
                logger.warning(
                    "booking.notify.no_recipients tenant=%s appointment_id=%s "
                    "specialist_id=%s — no linked master chat, no manager_chat_id, "
                    "no HANDOFF_NOTIFY_MAX_CHAT_IDS: nobody was told about this booking",
                    tenant.slug,
                    appointment_id,
                    specialist_id,
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
