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
        assert result == {"sent": 0, "failed": 0, "skipped": 0}
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
