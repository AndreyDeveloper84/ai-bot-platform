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
from apps.booking.models import BookingReminder, PendingBookingAction
from apps.bookings.keyboards import (
    CALLBACK_BOOK_CANCEL_PREFIX,
    CALLBACK_BOOK_CONFIRM_PREFIX,
    CALLBACK_CANCEL_PREFIX,
    CALLBACK_CONFIRM_PREFIX,
    CALLBACK_RESCHEDULE_PREFIX,
)
from apps.bookings.pending_actions import consume_pending, discard_pending
from apps.events.services import emit
from apps.skills.base import SkillContext, SkillResult
from apps.skills.booking.tools import (
    execute_cancel,
    execute_confirm,
    execute_reschedule,
)
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

# B5 / DRF-841 — replies for the 2-button preview gate.
REPLY_BOOK_EXPIRED = "Слишком много времени прошло — давайте подберём слот заново."
REPLY_BOOK_CANCELLED_PREVIEW = "Ок, не записываю."
REPLY_BOOK_ALREADY_HANDLED = "Это действие уже выполнено."
REPLY_BOOK_PARTIAL_FAILURE = "Не удалось завершить перенос — передал администратору, он свяжется."
REPLY_BOOK_CANCEL_FAILED = "Не получилось отменить запись — передал администратору, скоро уточнит."


# Audit slugs. Out-of-canonical-vocab; the audit subsystem accepts
# any string (vocabulary check is on the Event emit path).
AUDIT_REMINDER_CONFIRMED = "bookings.reminder.confirmed"
AUDIT_REMINDER_CANCELLED = "bookings.reminder.cancelled"
AUDIT_REMINDER_RESCHEDULE = "bookings.reminder.reschedule_requested"
AUDIT_REMINDER_REPLAY = "bookings.reminder.callback_replay"
AUDIT_REMINDER_FORBIDDEN = "bookings.reminder.callback_forbidden"

# B5 — preview-gate callback slugs.
AUDIT_BOOK_GATE_EXECUTED = "bookings.gate.executed"
AUDIT_BOOK_GATE_CANCELLED = "bookings.gate.cancelled"
AUDIT_BOOK_GATE_EXPIRED = "bookings.gate.expired"
AUDIT_BOOK_GATE_REPLAY = "bookings.gate.callback_replay"
AUDIT_BOOK_GATE_FORBIDDEN = "bookings.gate.callback_forbidden"


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


def _parse_gate_token(callback_text: str) -> tuple[str, UUID] | None:
    """Decode ``cb:book:{action}:{token}`` → (action, UUID).

    Returns ``None`` for malformed input (unrecognised prefix or the
    token segment isn't a valid UUID). Same idiom as
    :func:`_parse_reminder_pk` for symmetry.
    """
    for action, prefix in (
        ("confirm", CALLBACK_BOOK_CONFIRM_PREFIX),
        ("cancel", CALLBACK_BOOK_CANCEL_PREFIX),
    ):
        if callback_text.startswith(prefix):
            raw = callback_text[len(prefix) :].strip()
            try:
                return action, UUID(raw)
            except (ValueError, TypeError):
                return None
    return None


@register
class BookingGateCallbackSkill:
    """Handle the 2-button preview-gate callbacks (B5 / DRF-841).

    Callback namespace: ``cb:book:confirm:<token>`` and
    ``cb:book:cancel:<token>``. The ``<token>`` segment is the UUID pk
    of a :class:`apps.booking.models.PendingBookingAction` row created
    at preview time by one of the three destructive tools
    (``confirm_booking`` / ``cancel_booking`` / ``reschedule_booking``).

    ### Flow on ``cb:book:confirm:<token>``

    1. Parse token; reject malformed payload with a polite reply.
    2. Lookup the pending row (``all_tenants`` — token is globally
       unique by UUID); reject missing / expired / already-consumed
       with the corresponding canned reply.
    3. Authorise: the row's ``bot_user`` must match the sender's
       BotUser pk. Mismatch → forbidden reply + audit.
    4. CAS-consume the row via :func:`consume_pending`. Loser of a
       race short-circuits to "already handled".
    5. Dispatch to the matching ``execute_*`` function in
       :mod:`apps.skills.booking.tools` with the YClients client.
    6. On YClients failure: handoff. On reschedule partial failure:
       handoff + manager notification.

    ### Flow on ``cb:book:cancel:<token>``

    1. Parse token.
    2. Discard the pending row (no destructive call).
    3. Reply "ок, не записываю".

    Symmetric design with :class:`BookingReminderCallbackSkill`: same
    skill-registry registration, same matches() predicate shape, same
    authorisation principle.
    """

    name: ClassVar[str] = "booking_gate_callback"

    def matches(self, context: SkillContext) -> bool:
        text = (context.message_text or "").strip()
        return text.startswith(CALLBACK_BOOK_CONFIRM_PREFIX) or text.startswith(
            CALLBACK_BOOK_CANCEL_PREFIX
        )

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        parsed = _parse_gate_token(text)
        if parsed is None:
            logger.info("bookings.gate.malformed text=%r", text)
            return SkillResult(reply_text=REPLY_NOT_FOUND)

        action, token = parsed
        if action == "cancel":
            return self._handle_cancel_tap(context, token)
        # action == "confirm"
        return self._handle_confirm_tap(context, token)

    # ─── confirm-tap (executes the destructive verb) ─────────────────────

    def _handle_confirm_tap(
        self,
        context: SkillContext,
        token: UUID,
    ) -> SkillResult:
        """Execute the pending action when the user taps ✅.

        Each error branch maps cleanly to one canned reply — keeps the
        UX deterministic regardless of which destructive verb is
        underneath.

        Authorisation check fires BEFORE the consume CAS: a foreign
        user tapping a leaked token must NOT race-claim the row out
        from under the legitimate owner. Without this ordering, the
        foreign user's tap would consume the row, then fail the
        ownership check; the legitimate owner's next tap would then
        get "already handled" — bad UX + security smell.
        """
        # Pre-lookup so we can ownership-check before CAS-consuming.
        try:
            row = PendingBookingAction.all_tenants.get(pk=token)
        except PendingBookingAction.DoesNotExist:
            return SkillResult(reply_text=REPLY_NOT_FOUND)

        if not _gate_tenant_matches(row, context.bot_user):
            # Bookings/callbacks retro #3: defence-in-depth tenant guard.
            # Today the bot_user ownership gate below catches every cross-
            # tenant case because bot_users are partitioned per-tenant.
            # If a future misrouted webhook ever lands a PendingBookingAction
            # with the wrong tenant_id (or a same-channel_user_id collision
            # across tenants ever surfaces), this assert turns silent data
            # corruption into a loud REPLY_FORBIDDEN + audit row.
            write_audit(
                action=AUDIT_BOOK_GATE_FORBIDDEN,
                target="PendingBookingAction",
                target_id=row.pk,
                payload={
                    "sender_id": str(context.bot_user.pk),
                    "reason": "tenant_mismatch",
                    "row_tenant_id": str(row.tenant_id),
                    "sender_tenant_id": str(context.bot_user.tenant_id),
                },
            )
            return SkillResult(reply_text=REPLY_FORBIDDEN)

        if not _gate_sender_matches(row, context.bot_user.pk):
            write_audit(
                action=AUDIT_BOOK_GATE_FORBIDDEN,
                target="PendingBookingAction",
                target_id=row.pk,
                payload={"sender_id": str(context.bot_user.pk)},
            )
            return SkillResult(reply_text=REPLY_FORBIDDEN)

        lookup = consume_pending(token)
        if lookup.row is None:
            return SkillResult(reply_text=REPLY_NOT_FOUND)

        if lookup.expired:
            write_audit(
                action=AUDIT_BOOK_GATE_EXPIRED,
                target="PendingBookingAction",
                target_id=lookup.row.pk,
                payload={"kind": lookup.row.kind},
            )
            return SkillResult(reply_text=REPLY_BOOK_EXPIRED)

        if lookup.already_consumed:
            write_audit(
                action=AUDIT_BOOK_GATE_REPLAY,
                target="PendingBookingAction",
                target_id=lookup.row.pk,
                payload={"kind": lookup.row.kind},
            )
            return SkillResult(reply_text=REPLY_BOOK_ALREADY_HANDLED)

        # We claimed the row. Execute the matching verb.
        row = lookup.row
        try:
            from apps.integrations.yclients import get_yclients_client

            client = get_yclients_client()
        except Exception as exc:  # noqa: BLE001 — misconfigured integration
            logger.warning("bookings.gate.yclients_init_failed err=%s", exc)
            return SkillResult(
                reply_text=REPLY_BOOK_CANCEL_FAILED
                if row.kind != PendingBookingAction.Kind.CONFIRM
                else "Сейчас не могу записать, попробуйте чуть позже.",
                should_handoff=True,
                handoff_reason="booking_yclients_failure",
            )

        if row.kind == PendingBookingAction.Kind.CONFIRM:
            return self._dispatch_confirm(client, row)
        if row.kind == PendingBookingAction.Kind.CANCEL:
            return self._dispatch_cancel(client, row)
        # RESCHEDULE
        return self._dispatch_reschedule(client, row)

    def _dispatch_confirm(
        self,
        client: object,
        row: PendingBookingAction,
    ) -> SkillResult:
        result = execute_confirm(
            client=client,
            payload=row.payload,
            tenant=row.tenant,
            bot_user=row.bot_user,
        )
        if result.error in {"yclients_unavailable", "yclients_api_error"}:
            return SkillResult(
                reply_text="Не удалось создать запись — переключаю на менеджера.",
                should_handoff=True,
                handoff_reason="booking_yclients_failure",
            )
        if result.error:
            return SkillResult(
                reply_text="Не удалось создать запись — переключаю на менеджера.",
                should_handoff=True,
                handoff_reason="booking_yclients_failure",
            )
        write_audit(
            action=AUDIT_BOOK_GATE_EXECUTED,
            target="PendingBookingAction",
            target_id=row.pk,
            payload={"kind": "confirm"},
        )
        return SkillResult(reply_text=result.text)

    def _dispatch_cancel(
        self,
        client: object,
        row: PendingBookingAction,
    ) -> SkillResult:
        result = execute_cancel(
            client=client,
            payload=row.payload,
            tenant=row.tenant,
            bot_user=row.bot_user,
        )
        if result.error == "yclients_cancel_failure":
            return SkillResult(
                reply_text=REPLY_BOOK_CANCEL_FAILED,
                should_handoff=True,
                handoff_reason="booking_cancel_yclients_failure",
            )
        if result.error == "invalid_record_id":
            return SkillResult(
                reply_text=REPLY_NOT_FOUND,
                should_handoff=True,
                handoff_reason="booking_invalid_record_id",
            )
        if result.error:
            return SkillResult(
                reply_text=REPLY_BOOK_CANCEL_FAILED,
                should_handoff=True,
                handoff_reason="booking_cancel_yclients_failure",
            )
        write_audit(
            action=AUDIT_BOOK_GATE_EXECUTED,
            target="PendingBookingAction",
            target_id=row.pk,
            payload={"kind": "cancel"},
        )
        return SkillResult(reply_text=result.text)

    def _dispatch_reschedule(
        self,
        client: object,
        row: PendingBookingAction,
    ) -> SkillResult:
        result = execute_reschedule(
            client=client,
            payload=row.payload,
            tenant=row.tenant,
            bot_user=row.bot_user,
        )
        if result.error == "booking_reschedule_partial_failure":
            # Notify the salon manager so an operator can manually
            # re-book — the user already lost their original slot.
            _notify_manager_partial_reschedule(row)
            return SkillResult(
                reply_text=REPLY_BOOK_PARTIAL_FAILURE,
                should_handoff=True,
                handoff_reason="booking_reschedule_partial_failure",
            )
        if result.error == "yclients_cancel_failure":
            return SkillResult(
                reply_text=REPLY_BOOK_CANCEL_FAILED,
                should_handoff=True,
                handoff_reason="booking_cancel_yclients_failure",
            )
        if result.error == "invalid_record_id":
            return SkillResult(
                reply_text=REPLY_NOT_FOUND,
                should_handoff=True,
                handoff_reason="booking_invalid_record_id",
            )
        if result.error:
            return SkillResult(
                reply_text=REPLY_BOOK_CANCEL_FAILED,
                should_handoff=True,
                handoff_reason="booking_yclients_failure",
            )

        # Success — also notify the manager (informational, not a
        # handoff) so the master is in the loop about the moved slot.
        _notify_manager_reschedule_success(row, result)
        write_audit(
            action=AUDIT_BOOK_GATE_EXECUTED,
            target="PendingBookingAction",
            target_id=row.pk,
            payload={"kind": "reschedule"},
        )
        return SkillResult(reply_text=result.text)

    # ─── cancel-tap (discard preview, no destructive call) ───────────────

    def _handle_cancel_tap(
        self,
        context: SkillContext,
        token: UUID,
    ) -> SkillResult:
        # Look up first so we can authorisation-check + tell apart
        # already-consumed / expired / unknown. Authoritative state
        # change happens in :func:`discard_pending`.
        try:
            row = PendingBookingAction.all_tenants.get(pk=token)
        except PendingBookingAction.DoesNotExist:
            return SkillResult(reply_text=REPLY_NOT_FOUND)

        # Symmetric tenant guard (see retro #3 note in _handle_confirm_tap).
        if not _gate_tenant_matches(row, context.bot_user):
            write_audit(
                action=AUDIT_BOOK_GATE_FORBIDDEN,
                target="PendingBookingAction",
                target_id=row.pk,
                payload={
                    "sender_id": str(context.bot_user.pk),
                    "reason": "tenant_mismatch",
                    "row_tenant_id": str(row.tenant_id),
                    "sender_tenant_id": str(context.bot_user.tenant_id),
                },
            )
            return SkillResult(reply_text=REPLY_FORBIDDEN)

        if not _gate_sender_matches(row, context.bot_user.pk):
            write_audit(
                action=AUDIT_BOOK_GATE_FORBIDDEN,
                target="PendingBookingAction",
                target_id=row.pk,
                payload={"sender_id": str(context.bot_user.pk)},
            )
            return SkillResult(reply_text=REPLY_FORBIDDEN)

        if row.consumed_at is not None:
            return SkillResult(reply_text=REPLY_BOOK_ALREADY_HANDLED)

        ok = discard_pending(token)
        if not ok:
            # Either expired between the lookup and the discard CAS,
            # or another tap raced and already consumed it. Either
            # way, "already handled" is the right message.
            return SkillResult(reply_text=REPLY_BOOK_ALREADY_HANDLED)

        write_audit(
            action=AUDIT_BOOK_GATE_CANCELLED,
            target="PendingBookingAction",
            target_id=row.pk,
            payload={"kind": row.kind},
        )
        return SkillResult(reply_text=REPLY_BOOK_CANCELLED_PREVIEW)


def _gate_tenant_matches(
    row: PendingBookingAction,
    bot_user: object,
) -> bool:
    """Defence-in-depth tenant check (Bookings/callbacks retro #3).

    A PendingBookingAction is keyed on a globally-unique UUID and looked
    up via ``all_tenants``. The downstream ``execute_*`` tools trust
    ``row.tenant`` blindly — so if a future misrouted webhook ever lands
    a row with the wrong tenant, the callback would happily execute it
    cross-tenant because the ownership gate (`_gate_sender_matches`)
    only verifies ``bot_user``. ``bot_user`` partitions per-tenant
    today, but we don't want this property to be load-bearing across
    future BotUser refactors.

    Returns ``True`` when ``row.tenant_id`` matches the sender's
    ``bot_user.tenant_id``. Caller renders REPLY_FORBIDDEN on mismatch.

    Both sides are required to be non-None: a defence-in-depth gate that
    silently passes on ``None == None`` is the wrong default. Production
    webhooks always carry a tenant; a None-bearing sender (synthetic,
    misconfigured) MUST fall into the reject branch.
    """
    row_tid = getattr(row, "tenant_id", None)
    sender_tid = getattr(bot_user, "tenant_id", None)
    return row_tid is not None and row_tid == sender_tid


def _gate_sender_matches(
    row: PendingBookingAction,
    sender_pk: object,
) -> bool:
    """Authorisation check — sender must own the pending row.

    Compare stringified UUIDs to dodge UUID-vs-str confusion across
    caller contexts (same idiom as :func:`_sender_matches` for the
    reminder family).
    """
    target_pk = getattr(row.bot_user_id, "hex", None) or str(row.bot_user_id)
    incoming_pk = getattr(sender_pk, "hex", None) or str(sender_pk)
    return target_pk == incoming_pk


def _notify_manager_partial_reschedule(row: PendingBookingAction) -> None:
    """Tell the salon manager about a reschedule partial failure.

    Best-effort outbound to ``tenant.manager_chat_id``; failure to send
    must NOT raise from the callback path (the destructive operation
    already happened, the user's reply is already on the line).
    """
    tenant = row.tenant
    chat_id = (tenant.manager_chat_id or "").strip()
    if not chat_id:
        logger.warning(
            "bookings.gate.partial.no_manager_chat tenant=%s pk=%s",
            tenant.slug,
            row.pk,
        )
        return
    text = _render_partial_failure_text(row)
    try:
        from apps.channels.max.outbound import send_message

        send_message(chat_id=chat_id, text=text, attachments=None)
    except Exception:  # noqa: BLE001 — manager-notification is best-effort
        logger.exception("bookings.gate.partial.notify_failed pk=%s", row.pk)


def _notify_manager_reschedule_success(
    row: PendingBookingAction,
    result: object,
) -> None:
    """Tell the salon manager about a successful reschedule.

    Informational only — best-effort, same as the partial-failure
    notification.
    """
    tenant = row.tenant
    chat_id = (tenant.manager_chat_id or "").strip()
    if not chat_id:
        return
    text = _render_reschedule_success_text(row, result)
    try:
        from apps.channels.max.outbound import send_message

        send_message(chat_id=chat_id, text=text, attachments=None)
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("bookings.gate.reschedule.notify_failed pk=%s", row.pk)


def _render_partial_failure_text(row: PendingBookingAction) -> str:
    payload = row.payload or {}
    return (
        "⚠️ Перенос записи не завершён.\n"
        f"Старая запись (record_id={payload.get('record_id', '—')}) отменена, "
        f"новая на {payload.get('new_datetime', '—')} НЕ создана.\n"
        f"Услуга: {payload.get('service_name', '—')}\n"
        f"Мастер: {payload.get('master_name', '—')}\n"
        f"Клиент: {payload.get('client_name', '—')} "
        f"({payload.get('client_phone', '—')})\n"
        "Пожалуйста, перезапишите клиента вручную."
    )


def _render_reschedule_success_text(
    row: PendingBookingAction,
    result: object,
) -> str:
    payload = row.payload or {}
    confirmation = getattr(result, "confirmation", None)
    new_record_id = getattr(confirmation, "record_id", "") if confirmation else ""
    return (
        "🔄 Клиент перенёс запись.\n"
        f"Старый record_id: {payload.get('record_id', '—')}\n"
        f"Новый record_id: {new_record_id or '—'}\n"
        f"Услуга: {payload.get('service_name', '—')}\n"
        f"Мастер: {payload.get('master_name', '—')}\n"
        f"Новое время: {payload.get('new_datetime', '—')}\n"
        f"Клиент: {payload.get('client_name', '—')} "
        f"({payload.get('client_phone', '—')})"
    )


def _try_yclients_cancel(yclients_record_id: str | None) -> bool:
    """Best-effort upstream cancel. Returns True on success.

    Wrapped in a broad exception handler — a YClients outage, a
    missing/misconfigured integration client, or a 4xx from YClients
    all map to ``False`` here. The local cancel already happened by
    the time this runs; the upstream call is informational.

    ``yclients_record_id`` is ``str | None`` because the column is
    nullable as of #442 (Ayla consumer path writes NULL). Pre-existing
    YClients rows still carry a string; Ayla rows skip the upstream
    cancel via the early-return below.
    """
    if not yclients_record_id:
        return False
    # Bookings/callbacks retro #6: distinguish «can't cancel because the
    # id is non-numeric» from «can't cancel because YClients is down».
    # Enterprise tenants are anticipated to grow opaque-id schemes (B2
    # model docstring); when that happens, ``int(yclients_record_id)``
    # would raise ValueError → broad except → looks identical to a
    # YClients outage in metrics/dashboards. Short-circuit early with a
    # distinct log line so the metric is honest.
    if not yclients_record_id.isdigit():
        logger.info(
            "bookings.callback.yclients_cancel_skipped_non_numeric yc_id=%s",
            yclients_record_id,
        )
        return False
    try:
        from apps.integrations.yclients import get_yclients_client

        client = get_yclients_client()
        # B1 (DRF-837) exposes ``cancel_record(record_id: int)``.
        # ``yclients_record_id`` is stored as a string per the B2 model
        # contract; isdigit() guard above ensures int() can't fail here.
        client.cancel_record(record_id=int(yclients_record_id))
        return True
    except Exception:  # noqa: BLE001 — best-effort by design
        logger.warning(
            "bookings.callback.yclients_cancel_failed yc_id=%s",
            yclients_record_id,
            exc_info=True,
        )
        return False
