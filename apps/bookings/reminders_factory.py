"""Reminder factory — single source of truth for scheduling.

DRF-844 / Phase 1 / R1. Both write paths (the B3 booking skill's
``confirm_booking`` tool and the B2 YClients admin webhook) route
through this factory so reminder-scheduling rules live in exactly one
place. Splitting these would let the two sites drift on:

* offset arithmetic (24h / 2h vs the spec)
* the chat_id snapshot policy (B2 uses ``bot_user.chat_id`` at write
  time; B3 must match or reminders would target the wrong destination
  after a channel-id change)
* idempotency keying (``(yclients_record_id, kind)`` pair)
* the ``status=PENDING`` reset semantic (re-confirm must un-cancel)

Centralising all of the above is the entire purpose of this module.

### Cross-tenant manager: deliberate

The factory uses :attr:`BookingReminder.all_tenants` because it's
called from contexts where ``current_tenant()`` may not be set
(notably the Celery beat-fed paths in Phase 2 and the YClients webhook
*before* :func:`apps.tenancy.context.tenant_scope` wraps the inner
handler in some edge cases). The ``tenant`` FK on the row is the
authoritative scope; the manager bypass is safe because every defaults
dict carries an explicit ``tenant=`` value the caller passed in.

### Empty chat_id is a soft no-op

If the BotUser has no ``chat_id``, the reminder can never deliver, so
writing the row would just create observability noise (a row that
``send_due_reminders`` would mark FAILED on the first pass). The
factory logs + returns ``[]`` instead. The B3 confirm tool and the B2
webhook both gate on chat_id earlier; this is defence-in-depth for
future callers (e.g. an admin "rebuild reminders for booking X" tool).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from apps.booking.models import BookingReminder

if TYPE_CHECKING:
    from uuid import UUID

    from apps.booking.models import BookingRequest
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Offset table. Order matters for the iteration: writing the
# DAY_BEFORE row first keeps the audit trail intuitive ("earliest
# reminder first") when a downstream consumer reads the audit log.
_REMINDER_OFFSETS: tuple[tuple[str, timedelta], ...] = (
    (BookingReminder.Kind.DAY_BEFORE, timedelta(hours=24)),
    (BookingReminder.Kind.TWO_HOURS, timedelta(hours=2)),
)


def create_reminders_for_booking(
    *,
    tenant: "Tenant",
    bot_user: "BotUser",
    yclients_record_id: str,
    visit_at: datetime,
    master_name: str,
    service_name: str,
    booking_request: "BookingRequest | None" = None,
) -> list[BookingReminder]:
    """Idempotently schedule both reminders for a confirmed booking.

    Args:
      tenant: owning :class:`apps.tenancy.models.Tenant`.
      bot_user: target client identity. Must have non-empty ``chat_id``
                or this is a no-op.
      yclients_record_id: stringified YClients booking record id —
                          half of the idempotency key.
      visit_at: tz-aware datetime of the actual salon visit. Offsets
                are computed against this.
      master_name: snapshot at write time. Used in the rendered
                   reminder text by :func:`apps.bookings.tasks.send_due_reminders`.
      service_name: snapshot at write time. Same role.
      booking_request: optional link to the :class:`BookingRequest`
                       row this reminder was created for (present on
                       the B2 webhook path, absent on B3 confirm path
                       — the B3 path creates the BookingRequest in a
                       separate ORM call, no FK back-link).

    Returns:
      The list of saved :class:`BookingReminder` rows
      (one per :class:`BookingReminder.Kind`). Empty when ``chat_id``
      was empty.

    Re-invocation: idempotent via
    ``update_or_create(yclients_record_id, kind, defaults={...})``.
    Calling this twice for the same booking refreshes the same two
    rows; ``status`` is forced back to ``PENDING`` (re-confirm should
    un-cancel a previously-cancelled-then-rebooked slot).
    """
    chat_id = getattr(bot_user, "chat_id", "") or ""
    if not chat_id:
        logger.info(
            "bookings.factory.skip_no_chat_id yc_id=%s bot_user=%s",
            yclients_record_id,
            getattr(bot_user, "id", None),
        )
        return []

    saved: list[BookingReminder] = []
    for kind, offset in _REMINDER_OFFSETS:
        defaults: dict[str, Any] = {
            "tenant": tenant,
            "bot_user": bot_user,
            "chat_id": chat_id,
            "visit_at": visit_at,
            "status": BookingReminder.Status.PENDING,
            "scheduled_at": visit_at - offset,
            "master_name": master_name,
            "service_name": service_name,
            "sent_at": None,
            "replied_at": None,
        }
        # Only set booking_request when caller passed one — None would
        # overwrite an existing link on the re-confirmation path.
        if booking_request is not None:
            defaults["booking_request"] = booking_request

        row, _created = BookingReminder.all_tenants.update_or_create(
            yclients_record_id=yclients_record_id,
            kind=kind,
            defaults=defaults,
        )
        saved.append(row)

    logger.info(
        "bookings.factory.scheduled yc_id=%s rows=%d visit_at=%s",
        yclients_record_id,
        len(saved),
        visit_at.isoformat(),
    )
    return saved


def create_reminders_for_ayla_appointment(
    *,
    tenant: "Tenant",
    bot_user: "BotUser",
    appointment_id: "UUID",
    visit_at: datetime,
    master_name: str,
    service_name: str,
) -> list[BookingReminder]:
    """Напоминания записи Ayla из диалога — тем же ключом, что у потребителя событий (DRF-2586).

    Раньше диалог (``tools._schedule_reminders`` под ``BOOKING_VIA_AYLA_REST``)
    писал через :func:`create_reminders_for_booking`, то есть клал UUID записи
    Ayla в ``yclients_record_id``, а потребитель ``booking.created`` /
    ``booking.confirmed`` — в ``ayla_appointment_id``. Ключи разные, и у одной
    записи оказывалось ДВЕ пары напоминаний; на пилоте 13 пар, 4 доставлены
    обеими строками — человек получил одно и то же дважды.

    Теперь оба пишут ключом ``(ayla_appointment_id, tenant, kind)``, и двойника
    исключает частичный уникальный индекс ``unique_ayla_booking_reminder``
    (миграция 0012) — в базе, а не проверкой «есть ли уже» в двух воркерах, у
    которой гонка остаётся: событие может прийти раньше, чем диалог допишет.

    Два отличия от потребителя — намеренные, это разные поводы:

    * **Статус перевзводится.** Сюда приходят действия человека —
      подтверждение, перенос; перенос сначала гасит ``PENDING`` и заводит пару
      на новое время. Потребитель статус не трогает: повторная доставка события
      не должна воскрешать уже ушедшее напоминание.
    * **Имена мастера и услуги — снимок.** Потребитель пишет их только при
      создании строки и не затирает (``_schedule_reminders`` в
      ``apps/eventbus/consumers/booking.py``).

    Общее с потребителем: напоминание, чей срок уже прошёл, не заводится и не
    перевзводится (#1146) — иначе диспетчер отправил бы «завтра ваш визит»
    сразу, за час до визита.
    """
    from django.utils import timezone

    chat_id = getattr(bot_user, "chat_id", "") or ""
    if not chat_id:
        logger.info(
            "bookings.factory.ayla.skip_no_chat_id appointment_id=%s bot_user=%s",
            appointment_id,
            getattr(bot_user, "id", None),
        )
        return []

    now = timezone.now()
    saved: list[BookingReminder] = []
    for kind, offset in _REMINDER_OFFSETS:
        scheduled_at = visit_at - offset
        if scheduled_at <= now:
            logger.info(
                "bookings.factory.ayla.skip_backdated appointment_id=%s kind=%s",
                appointment_id,
                kind,
            )
            continue
        row, _created = BookingReminder.all_tenants.update_or_create(
            ayla_appointment_id=appointment_id,
            tenant=tenant,
            kind=kind,
            defaults={
                "bot_user": bot_user,
                # NULL, не "": иначе старый unique_together
                # (yclients_record_id, kind) столкнул бы разные записи Ayla.
                "yclients_record_id": None,
                "chat_id": chat_id,
                "visit_at": visit_at,
                "status": BookingReminder.Status.PENDING,
                "scheduled_at": scheduled_at,
                "master_name": master_name,
                "service_name": service_name,
                "sent_at": None,
                "replied_at": None,
            },
        )
        saved.append(row)

    logger.info(
        "bookings.factory.ayla.scheduled appointment_id=%s rows=%d visit_at=%s",
        appointment_id,
        len(saved),
        visit_at.isoformat(),
    )
    return saved
