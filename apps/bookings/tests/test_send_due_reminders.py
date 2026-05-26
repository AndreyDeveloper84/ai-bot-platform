"""Periodic-dispatcher tests (DRF-844 / Phase 1 / R1).

Covers:

* only PENDING + due rows picked (future-dated / non-PENDING skipped)
* DAY_BEFORE transitions to SENT_NO_REPLY; T-2h to SENT
* sent_at stamped on success
* Telegram (MAX) outbound failure → FAILED + audit row written
* race condition (mock CAS update to return 0 → row skipped)
* batch limit respected
* T-24h send carries inline-keyboard attachment; T-2h does not
* return-dict counters correct
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder
from apps.bookings import tasks as dispatcher_tasks
from apps.bookings.tasks import send_due_reminders
from apps.channels.max.outbound import MaxAPIError
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rem-disp", name="Salon Disp")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-d",
        chat_id="chat-d",
        phone="79991234567",
        client_name="Anna",
    )


def _make_reminder(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    kind: str,
    yc_id: str,
    scheduled_at,
    status: str = BookingReminder.Status.PENDING,
) -> BookingReminder:
    """Helper — write a single reminder row matching the factory shape."""
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        chat_id=bot_user.chat_id,
        visit_at=scheduled_at + timedelta(hours=24),
        kind=kind,
        status=status,
        scheduled_at=scheduled_at,
        master_name="Lera",
        service_name="Массаж",
    )


class TestPickup:
    def test_only_pending_due_rows_picked(self, tenant: Tenant, bot_user: BotUser) -> None:
        past = timezone.now() - timedelta(minutes=5)
        future = timezone.now() + timedelta(hours=1)

        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-due",
            scheduled_at=past,
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-future",
            scheduled_at=future,
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-already-sent",
            scheduled_at=past,
            status=BookingReminder.Status.SENT_NO_REPLY,
        )

        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        assert result["sent"] == 1
        assert mock_send.call_count == 1
        # The future-scheduled row is still PENDING.
        future_row = BookingReminder.all_tenants.get(yclients_record_id="yc-future")
        assert future_row.status == BookingReminder.Status.PENDING

    def test_future_only_picks_nothing(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-fut",
            scheduled_at=timezone.now() + timedelta(hours=2),
        )
        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        assert result == {
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "stale": 0,
            "deferred": 0,
        }
        mock_send.assert_not_called()


class TestTransitions:
    def test_day_before_transitions_to_sent_no_reply(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        row = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-1",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        with patch("apps.bookings.tasks.send_message"):
            send_due_reminders()
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.SENT_NO_REPLY
        assert row.sent_at is not None

    def test_two_hours_transitions_to_sent(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.TWO_HOURS,
            yc_id="yc-2",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        with patch("apps.bookings.tasks.send_message"):
            send_due_reminders()
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.SENT
        assert row.sent_at is not None


class TestKeyboardAttachment:
    def test_day_before_includes_buttons(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-1",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        with patch("apps.bookings.tasks.send_message") as mock_send:
            send_due_reminders()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["attachments"] is not None
        # 3 buttons inside the inline keyboard envelope.
        buttons = kwargs["attachments"][0]["payload"]["buttons"]
        assert len(buttons) == 3
        callbacks = [b["callback"] for b in buttons]
        assert any(c.startswith("cb:rem:confirm:") for c in callbacks)
        assert any(c.startswith("cb:rem:cancel:") for c in callbacks)
        assert any(c.startswith("cb:rem:reschedule:") for c in callbacks)

    def test_two_hours_no_attachments(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.TWO_HOURS,
            yc_id="yc-2",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        with patch("apps.bookings.tasks.send_message") as mock_send:
            send_due_reminders()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["attachments"] is None


class TestFailurePath:
    def test_send_failure_flips_to_failed(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-fail",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        with patch(
            "apps.bookings.tasks.send_message",
            side_effect=MaxAPIError(503, "service unavailable"),
        ):
            result = send_due_reminders()
        assert result["failed"] == 1
        assert result["sent"] == 0
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.FAILED
        # sent_at NOT stamped on failure.
        assert row.sent_at is None

    def test_unexpected_exception_also_flips_to_failed(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        row = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-unexp",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        with patch(
            "apps.bookings.tasks.send_message",
            side_effect=RuntimeError("boom"),
        ):
            result = send_due_reminders()
        assert result["failed"] == 1
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.FAILED


class TestRaceCondition:
    def test_lost_race_skips_send(self, tenant: Tenant, bot_user: BotUser) -> None:
        """If the CAS update returns rowcount=0, the worker skips
        (another worker won the race). Verify by intercepting the
        ``.update(status=...)`` call.
        """
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-race",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )

        # Wrap the manager's filter().update() chain to always return 0
        # for the first (CAS) call. We achieve this by monkey-patching
        # the BookingReminder.all_tenants manager — the simpler patch
        # of the QuerySet.update method risks colliding with the
        # success-path stamping update.
        original_filter = BookingReminder.all_tenants.filter

        class FakeQS:
            def __init__(self, real):
                self._real = real

            def update(self, **kwargs):
                # First call is the CAS that should "lose the race".
                if "status" in kwargs and len(kwargs) == 1:
                    return 0
                return self._real.update(**kwargs)

            def __getattr__(self, name):
                return getattr(self._real, name)

            def __iter__(self):
                return iter(self._real)

        def fake_filter(*args, **kwargs):
            return FakeQS(original_filter(*args, **kwargs))

        with (
            patch.object(BookingReminder.all_tenants, "filter", side_effect=fake_filter),
            patch("apps.bookings.tasks.send_message") as mock_send,
        ):
            result = send_due_reminders()

        # No send happened — CAS returned 0 across the board.
        assert mock_send.call_count == 0
        assert result["skipped"] >= 1
        assert result["sent"] == 0


class TestBatchLimit:
    def test_batch_limit_enforced(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Don't process more than BATCH_LIMIT rows per tick — defends
        against backlog-after-outage drain spikes."""
        # Insert BATCH_LIMIT+2 due rows. Use distinct yc_ids (the
        # unique_together(yc_id, kind) constraint forbids duplicates).
        scheduled = timezone.now() - timedelta(minutes=5)
        for i in range(dispatcher_tasks.BATCH_LIMIT + 2):
            _make_reminder(
                tenant=tenant,
                bot_user=bot_user,
                kind=BookingReminder.Kind.DAY_BEFORE,
                yc_id=f"yc-batch-{i}",
                scheduled_at=scheduled,
            )
        # Drop the cap for a fast test (1 second worth of work, not
        # 200 individual sends).
        with (
            patch.object(dispatcher_tasks, "BATCH_LIMIT", 5),
            patch("apps.bookings.tasks.send_message") as mock_send,
        ):
            result = send_due_reminders()
        assert mock_send.call_count == 5
        assert result["sent"] == 5


# ───────────────────────────────────────────────────────────────────────
# Send-time booking-state re-check invariant (P0 PRE_PILOT)
# ───────────────────────────────────────────────────────────────────────


def _make_linked_reminder(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    booking_request,
    kind: str = BookingReminder.Kind.DAY_BEFORE,
    scheduled_at=None,
) -> BookingReminder:
    """Helper — write a reminder linked к an existing BookingRequest row."""
    if scheduled_at is None:
        scheduled_at = timezone.now() - timedelta(minutes=5)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        booking_request=booking_request,
        yclients_record_id=None,
        chat_id=bot_user.chat_id,
        visit_at=scheduled_at + timedelta(hours=24),
        kind=kind,
        status=BookingReminder.Status.PENDING,
        scheduled_at=scheduled_at,
        master_name=booking_request.master_name or "Lera",
        service_name=booking_request.service_name,
    )


def _make_booking(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    status: str | None = None,
    completed_at=None,
):
    """Helper — write a BookingRequest row для re-check tests."""
    from apps.booking.models import BookingRequest

    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        service_name="Массаж",
        master_name="Lera",
        client_name="Anna",
        client_phone="79991234567",
        status=status or BookingRequest.Status.CONFIRMED,
        completed_at=completed_at,
    )


class TestSendTimeRecheck:
    """P0 PRE_PILOT — founder pilot-scope sequence #2.

    Re-fetch BookingRequest at dispatch and drop / defer reminders
    when underlying state changed. «Ayla напомнила о записи которую
    я отменила» = trust break we must prevent.
    """

    def test_confirmed_booking_sends_normally(self, tenant: Tenant, bot_user: BotUser) -> None:
        booking = _make_booking(tenant=tenant, bot_user=bot_user)
        _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        assert mock_send.call_count == 1
        assert result["sent"] == 1
        assert result["stale"] == 0
        assert result["deferred"] == 0

    def test_cancelled_booking_drops_to_stale_dropped(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        from apps.booking.models import BookingRequest

        booking = _make_booking(
            tenant=tenant, bot_user=bot_user, status=BookingRequest.Status.CANCELLED
        )
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        assert mock_send.call_count == 0
        assert result["sent"] == 0
        assert result["stale"] == 1
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.STALE_DROPPED

    def test_rescheduled_booking_drops_to_stale_dropped(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        from apps.booking.models import BookingRequest

        booking = _make_booking(
            tenant=tenant, bot_user=bot_user, status=BookingRequest.Status.RESCHEDULED
        )
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message") as mock_send:
            send_due_reminders()
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.STALE_DROPPED
        mock_send.assert_not_called()

    def test_completed_at_set_drops_to_stale_dropped(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Visit happened before T-2h reminder dispatched — drop, no send."""
        booking = _make_booking(
            tenant=tenant,
            bot_user=bot_user,
            completed_at=timezone.now() - timedelta(hours=1),
        )
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        assert result["stale"] == 1
        mock_send.assert_not_called()
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.STALE_DROPPED

    def test_cancel_requested_interim_defers_without_cas(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """CANCEL_REQUESTED is reversible within ~5s undo window.
        Defer — leave PENDING — next 15-min tick re-checks."""
        from apps.booking.models import BookingRequest

        booking = _make_booking(
            tenant=tenant,
            bot_user=bot_user,
            status=BookingRequest.Status.CANCEL_REQUESTED,
        )
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        mock_send.assert_not_called()
        assert result["deferred"] == 1
        assert result["sent"] == 0
        assert result["stale"] == 0
        reminder.refresh_from_db()
        # Critically — STAYS PENDING.
        assert reminder.status == BookingReminder.Status.PENDING

    def test_reschedule_requested_interim_defers(self, tenant: Tenant, bot_user: BotUser) -> None:
        from apps.booking.models import BookingRequest

        booking = _make_booking(
            tenant=tenant,
            bot_user=bot_user,
            status=BookingRequest.Status.RESCHEDULE_REQUESTED,
        )
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        mock_send.assert_not_called()
        assert result["deferred"] == 1
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.PENDING

    def test_null_fk_reminder_sends_with_known_gap(self, tenant: Tenant, bot_user: BotUser) -> None:
        """D4 verdict — NULL booking_request FK = legacy row OR Ayla-path.
        Phase 0 pilot scope: send без re-check, document gap. Phase 1
        Ayla event-driven invalidation closes this."""
        # _make_reminder (without _linked_) creates NULL FK reminder.
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.DAY_BEFORE,
            yc_id="yc-nullfk",
            scheduled_at=timezone.now() - timedelta(minutes=5),
        )
        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        assert result["sent"] == 1
        mock_send.assert_called_once()

    def test_stale_drop_writes_audit_row(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Stale drop emits ``bookings.reminder.stale_dropped`` audit row
        с reason slug + booking_request_id. Forensic forensics required
        for post-pilot stale-rate analytics."""
        from apps.audit.models import AuditLog
        from apps.booking.models import BookingRequest

        booking = _make_booking(
            tenant=tenant, bot_user=bot_user, status=BookingRequest.Status.CANCELLED
        )
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message"):
            send_due_reminders()
        audit_rows = AuditLog.all_tenants.filter(
            action="bookings.reminder.stale_dropped",
            target_id=reminder.pk,
        )
        assert audit_rows.exists()
        payload = audit_rows.first().payload
        assert payload["reason"] == "booking_status_cancelled"
        assert payload["booking_request_id"] == str(booking.pk)
        assert payload["kind"] == BookingReminder.Kind.DAY_BEFORE


class TestRecheckAdversarial:
    """Phase F adversarial — race conditions + state-change-during-tick."""

    def test_row_already_stale_dropped_skipped_from_batch(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Row was flipped к STALE_DROPPED by another worker / earlier
        tick. Query filter (status=PENDING) excludes it от the batch;
        no audit double-emit. CR #851 finding: renamed для accuracy —
        true mid-loop CAS race needs threading; the inline-precedent
        CAS pattern is well-tested elsewhere в this module."""
        from apps.audit.models import AuditLog
        from apps.booking.models import BookingRequest

        booking = _make_booking(
            tenant=tenant, bot_user=bot_user, status=BookingRequest.Status.CANCELLED
        )
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        # Simulate другой worker won the race — flip reminder to
        # STALE_DROPPED before our tick runs the CAS.
        BookingReminder.all_tenants.filter(pk=reminder.pk).update(
            status=BookingReminder.Status.STALE_DROPPED
        )
        # NOTE: our query selects status=PENDING — the row above is no
        # longer pending, so it won't even appear в the batch. To
        # simulate a true mid-loop race, we'd need threading. But the
        # CAS pattern is well-tested elsewhere; here we verify that
        # the «row gone из PENDING» case doesn't emit a duplicate
        # audit row by querying any audit emitted for this reminder.
        with patch("apps.bookings.tasks.send_message"):
            send_due_reminders()
        audit_rows = AuditLog.all_tenants.filter(
            action="bookings.reminder.stale_dropped",
            target_id=reminder.pk,
        )
        # Our task didn't emit since the row wasn't in the batch.
        assert audit_rows.count() == 0

    def test_unknown_future_booking_status_defers(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Future Ayla states (provider_cancelled, no_show, etc.) may
        arrive via mirror sync before our enum learns about them.
        Defensive default = defer, not send, не drop."""
        booking = _make_booking(tenant=tenant, bot_user=bot_user)
        # Bypass enum validation by writing raw — simulates a future
        # status sneak-in.
        from apps.booking.models import BookingRequest

        BookingRequest.all_tenants.filter(pk=booking.pk).update(status="provider_cancelled")
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message") as mock_send:
            result = send_due_reminders()
        mock_send.assert_not_called()
        # Defer: row stays PENDING, doesn't get marked stale.
        assert result["deferred"] == 1
        assert result["stale"] == 0
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.PENDING

    def test_completed_at_overrides_status(self, tenant: Tenant, bot_user: BotUser) -> None:
        """``completed_at`` check fires BEFORE status check — a CONFIRMED
        booking whose visit already happened still drops."""
        booking = _make_booking(
            tenant=tenant,
            bot_user=bot_user,
            completed_at=timezone.now() - timedelta(hours=1),
        )
        reminder = _make_linked_reminder(tenant=tenant, bot_user=bot_user, booking_request=booking)
        with patch("apps.bookings.tasks.send_message"):
            result = send_due_reminders()
        assert result["stale"] == 1
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.STALE_DROPPED
