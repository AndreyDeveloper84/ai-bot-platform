"""Callback skill for reminder buttons.

DRF-844 / Phase 1 / R1. Owns the three ``cb:rem:{action}:{pk}``
callbacks emitted by :func:`apps.bookings.keyboards.day_before_keyboard`:

* ``cb:rem:confirm:{pk}``     → PENDING reply received; client confirms.
  Status SENT_NO_REPLY → CONFIRMED. Reply: "Подтверждено, ждём вас!"
* ``cb:rem:cancel:{pk}``      → client cancels.
  Status SENT_NO_REPLY → CANCELLED. Reply: "Запись отменена…"
  Best-effort: cancel the YClients record server-side too. Wrapped
  in try/except so a YClients outage doesn't block the local cancel.
* ``cb:rem:reschedule:{pk}``  → client wants a new time.
  Status SENT_NO_REPLY → RESCHEDULE_REQUESTED. Reply: "Передал
  администратору…" + (TODO) operator notification.

### Why a Skill, not a dedicated callback router

The platform has no separate callback router today — callbacks come
into the channel handler as message_text starting with ``cb:`` and are
routed through the same skill registry that handles plain-text turns.
This is the same pattern used by ``cb:food:*`` (P1 / P5) and
``cb:anketa:*`` (P3). One mental model, one registration point.

### Idempotency

The handler verifies ``status == SENT_NO_REPLY`` BEFORE writing the
new status. A double-click on the same button (network jitter, user
re-tapping during a "loading" lag) hits the second handler call with
``status != SENT_NO_REPLY`` and short-circuits with a polite "уже
обработано" reply. This makes the click idempotent even though the
status transitions are not technically atomic compare-and-set (we use
``filter(pk=, status=SENT_NO_REPLY).update(status=...)`` for the
write, same atomic-CAS pattern as :mod:`apps.bookings.tasks`).

### Authorisation

The callback handler verifies the sender matches the reminder's
``bot_user``. Without that check, anyone who sniffed a callback
payload (forwarded chat, screenshare, etc.) could cancel someone
else's booking. The check is by ``channel_user_id`` — the natural key
for "who sent this message" in the platform's identity model.
"""

from __future__ import annotations

import logging
from typing import ClassVar
from uuid import UUID

from django.utils import timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingReminder
from apps.bookings.keyboards import (
    CALLBACK_CANCEL_PREFIX,
    CALLBACK_CONFIRM_PREFIX,
    CALLBACK_RESCHEDULE_PREFIX,
)
from apps.events.services import emit
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# Reply templates. Centralised so the tests assert against the
# constants (not duplicated string literals — refactor-safe).
REPLY_CONFIRMED = "Подтверждено, ждём вас!"
REPLY_CANCELLED = "Запись отменена, надеемся увидеть вас позже."
REPLY_RESCHEDULE = "Передал администратору, скоро напишут."

# Idempotency replies — when the user re-clicks after the row's
# already been transitioned out of SENT_NO_REPLY.
REPLY_ALREADY_HANDLED = "Эта запись уже обработана."

# Defensive replies for the corner cases (row not found, wrong user
# — these mostly happen via copy-pasted callback data in a developer
# context; the user-facing message is still polite and non-leaky).
REPLY_NOT_FOUND = "Не нашла эту запись — возможно, она уже снята."
REPLY_FORBIDDEN = "Эта запись не для этого профиля."


# Audit slugs. Out-of-canonical-vocab; the audit subsystem accepts
# any string (vocabulary check is on the Event emit path).
AUDIT_REMINDER_CONFIRMED = "bookings.reminder.confirmed"
AUDIT_REMINDER_CANCELLED = "bookings.reminder.cancelled"
AUDIT_REMINDER_RESCHEDULE = "bookings.reminder.reschedule_requested"
AUDIT_REMINDER_REPLAY = "bookings.reminder.callback_replay"
AUDIT_REMINDER_FORBIDDEN = "bookings.reminder.callback_forbidden"


def _parse_reminder_pk(callback_text: str) -> tuple[str, UUID] | None:
    """Decode ``cb:rem:{action}:{pk}`` → (action, UUID).

    Returns ``None`` for malformed input (not a recognised prefix,
    or the pk segment isn't a valid UUID). Callers route ``None`` to
    a "не понимаю" reply.
    """
    for action, prefix in (
        ("confirm", CALLBACK_CONFIRM_PREFIX),
        ("cancel", CALLBACK_CANCEL_PREFIX),
        ("reschedule", CALLBACK_RESCHEDULE_PREFIX),
    ):
        if callback_text.startswith(prefix):
            raw_pk = callback_text[len(prefix) :].strip()
            try:
                return action, UUID(raw_pk)
            except (ValueError, TypeError):
                return None
    return None


def _sender_matches(reminder: BookingReminder, bot_user_pk: object) -> bool:
    """Authorise: the caller's BotUser pk must own the reminder.

    BotUser pks are UUIDs in the platform; we compare the stringified
    forms to dodge any UUID-vs-str confusion between caller contexts.
    """
    target_pk = getattr(reminder.bot_user_id, "hex", None) or str(reminder.bot_user_id)
    incoming_pk = getattr(bot_user_pk, "hex", None) or str(bot_user_pk)
    return target_pk == incoming_pk


@register
class BookingReminderCallbackSkill:
    """Handle the three reminder-button callbacks."""

    name: ClassVar[str] = "booking_reminder_callback"

    def matches(self, context: SkillContext) -> bool:
        text = (context.message_text or "").strip()
        return text.startswith("cb:rem:")

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        parsed = _parse_reminder_pk(text)
        if parsed is None:
            logger.info("bookings.callback.malformed text=%r", text)
            return SkillResult(reply_text=REPLY_NOT_FOUND)

        action, pk = parsed
        # ``all_tenants`` because the conversation handler may not
        # have :mod:`apps.tenancy.context` set in the same way the
        # request middleware does for HTTP, and the lookup is keyed
        # on the UUID pk which is globally unique. The follow-up
        # ``_sender_matches`` check makes this safe — see module
        # docstring "Authorisation".
        try:
            reminder = BookingReminder.all_tenants.select_related(
                "tenant",
                "bot_user",
            ).get(pk=pk)
        except BookingReminder.DoesNotExist:
            logger.info("bookings.callback.not_found pk=%s action=%s", pk, action)
            return SkillResult(reply_text=REPLY_NOT_FOUND)

        if not _sender_matches(reminder, context.bot_user.pk):
            logger.warning(
                "bookings.callback.wrong_user pk=%s sender=%s owner=%s",
                pk,
                context.bot_user.pk,
                reminder.bot_user_id,
            )
            write_audit(
                action=AUDIT_REMINDER_FORBIDDEN,
                target="BookingReminder",
                target_id=reminder.pk,
                payload={"action": action, "sender_id": str(context.bot_user.pk)},
            )
            return SkillResult(reply_text=REPLY_FORBIDDEN)

        if reminder.status != BookingReminder.Status.SENT_NO_REPLY:
            # Idempotent replay — user re-clicked after the first
            # click already transitioned the row. Log + polite reply,
            # no state mutation, no second YClients API call.
            logger.info(
                "bookings.callback.replay pk=%s action=%s status=%s",
                pk,
                action,
                reminder.status,
            )
            write_audit(
                action=AUDIT_REMINDER_REPLAY,
                target="BookingReminder",
                target_id=reminder.pk,
                payload={"action": action, "current_status": reminder.status},
            )
            return SkillResult(reply_text=REPLY_ALREADY_HANDLED)

        if action == "confirm":
            return self._handle_confirm(reminder)
        if action == "cancel":
            return self._handle_cancel(reminder)
        # action == "reschedule" (only remaining; _parse_reminder_pk
        # exhausts the namespace)
        return self._handle_reschedule(reminder)

    # ─── per-action handlers ─────────────────────────────────────────────

    def _handle_confirm(self, reminder: BookingReminder) -> SkillResult:
        """SENT_NO_REPLY → CONFIRMED. Stamp replied_at."""
        now = timezone.now()
        rowcount = BookingReminder.all_tenants.filter(
            pk=reminder.pk,
            status=BookingReminder.Status.SENT_NO_REPLY,
        ).update(
            status=BookingReminder.Status.CONFIRMED,
            replied_at=now,
        )
        if rowcount == 0:
            # Concurrent click — the other branch won. Mirror the
            # replay path.
            return SkillResult(reply_text=REPLY_ALREADY_HANDLED)

        write_audit(
            action=AUDIT_REMINDER_CONFIRMED,
            target="BookingReminder",
            target_id=reminder.pk,
            payload={"yclients_record_id": reminder.yclients_record_id},
        )
        emit(
            AUDIT_REMINDER_CONFIRMED,
            properties={
                "yclients_record_id": reminder.yclients_record_id,
                "reminder_id": str(reminder.pk),
                "bot_user_id": str(reminder.bot_user_id),
            },
            distinct_id=str(reminder.bot_user_id),
        )
        return SkillResult(reply_text=REPLY_CONFIRMED)

    def _handle_cancel(self, reminder: BookingReminder) -> SkillResult:
        """SENT_NO_REPLY → CANCELLED. Best-effort YClients cancel."""
        now = timezone.now()
        rowcount = BookingReminder.all_tenants.filter(
            pk=reminder.pk,
            status=BookingReminder.Status.SENT_NO_REPLY,
        ).update(
            status=BookingReminder.Status.CANCELLED,
            replied_at=now,
        )
        if rowcount == 0:
            return SkillResult(reply_text=REPLY_ALREADY_HANDLED)

        # Best-effort upstream cancel. The B1 YClients client exposes
        # ``delete_record`` (per the integration's published shape);
        # importing lazily so a misconfigured (or absent) integration
        # client doesn't break local cancel.
        upstream_ok = _try_yclients_cancel(reminder.yclients_record_id)

        write_audit(
            action=AUDIT_REMINDER_CANCELLED,
            target="BookingReminder",
            target_id=reminder.pk,
            payload={
                "yclients_record_id": reminder.yclients_record_id,
                "yclients_cancel_ok": upstream_ok,
            },
        )
        emit(
            AUDIT_REMINDER_CANCELLED,
            properties={
                "yclients_record_id": reminder.yclients_record_id,
                "reminder_id": str(reminder.pk),
                "bot_user_id": str(reminder.bot_user_id),
                "yclients_cancel_ok": upstream_ok,
            },
            distinct_id=str(reminder.bot_user_id),
        )
        return SkillResult(reply_text=REPLY_CANCELLED)

    def _handle_reschedule(self, reminder: BookingReminder) -> SkillResult:
        """SENT_NO_REPLY → RESCHEDULE_REQUESTED. Defer operator-page TODO."""
        now = timezone.now()
        rowcount = BookingReminder.all_tenants.filter(
            pk=reminder.pk,
            status=BookingReminder.Status.SENT_NO_REPLY,
        ).update(
            status=BookingReminder.Status.RESCHEDULE_REQUESTED,
            replied_at=now,
        )
        if rowcount == 0:
            return SkillResult(reply_text=REPLY_ALREADY_HANDLED)

        # TODO(Phase 2): notify the operator chat that this reminder
        # needs manual rebooking. mysite did this via a direct
        # send_max_message to ADMIN_MAX_CHAT_ID; on the platform side
        # the same channel exists (settings.ADMIN_MAX_CHAT_ID) but the
        # "operator notification" lane is a separate skill/service
        # cross-cutting concern (see apps.handoff). Deferring to a
        # follow-up ticket — the audit row + canonical event below
        # are enough for an operator to spot the reschedule request
        # via the admin console in the meantime.

        write_audit(
            action=AUDIT_REMINDER_RESCHEDULE,
            target="BookingReminder",
            target_id=reminder.pk,
            payload={"yclients_record_id": reminder.yclients_record_id},
        )
        emit(
            AUDIT_REMINDER_RESCHEDULE,
            properties={
                "yclients_record_id": reminder.yclients_record_id,
                "reminder_id": str(reminder.pk),
                "bot_user_id": str(reminder.bot_user_id),
            },
            distinct_id=str(reminder.bot_user_id),
        )
        return SkillResult(reply_text=REPLY_RESCHEDULE)


def _try_yclients_cancel(yclients_record_id: str) -> bool:
    """Best-effort upstream cancel. Returns True on success.

    Wrapped in a broad exception handler — a YClients outage, a
    missing/misconfigured integration client, or a 4xx from YClients
    all map to ``False`` here. The local cancel already happened by
    the time this runs; the upstream call is informational.
    """
    if not yclients_record_id:
        return False
    try:
        from apps.integrations.yclients import get_yclients_client

        client = get_yclients_client()
        # B1 (DRF-837) exposes ``cancel_record(record_id: int)``. The
        # stored ``yclients_record_id`` is a string per the B2 model
        # contract (handles future enterprise-tenant opaque ids), so
        # we coerce on call. A non-numeric value silently falls into
        # the except branch — best-effort.
        client.cancel_record(record_id=int(yclients_record_id))
        return True
    except Exception:  # noqa: BLE001 — best-effort by design
        logger.warning(
            "bookings.callback.yclients_cancel_failed yc_id=%s",
            yclients_record_id,
            exc_info=True,
        )
        return False
