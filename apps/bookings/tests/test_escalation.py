"""Escalation-beat tests (DRF-845 / Phase 1 / R2).

Covers all 8 spec behaviours:

1. Happy path — DAY_BEFORE / SENT_NO_REPLY / visit_at = now+6h escalates.
2. Filter: kind=TWO_HOURS never escalated.
3. Filter: status != SENT_NO_REPLY never escalated.
4. Filter: visit_at > now+12h never escalated yet.
5. Idempotency — second run is a no-op (ESCALATED is terminal).
6. Multi-tenant fan-out — per-tenant manager_chat_id, no cross-leak.
7. Empty manager_chat_id — status flips, no MAX send, WARN logged.
8. MAX send failure — status reverts to SENT_NO_REPLY for retry.

Plus a few defensive cases (race condition, batch limit, message
content sanity).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, BookingRequest
from apps.bookings import escalation as escalation_mod
from apps.bookings.escalation import escalate_stale_reminders
from apps.channels.max.outbound import MaxAPIError
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="rem-esc",
        name="Salon Escalation",
        manager_chat_id="mgr-chat-1",
    )


@pytest.fixture
def tenant_no_manager(db) -> Tenant:
    return Tenant.objects.create(
        slug="rem-esc-empty",
        name="Salon No Manager",
        manager_chat_id="",
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-e",
        chat_id="chat-client-1",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def bot_user_no_manager(tenant_no_manager: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_no_manager,
        channel="max",
        channel_user_id="bu-e-nom",
        chat_id="chat-client-nom",
        phone="79990000000",
        client_name="Bob",
    )


def _make_reminder(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    kind: str = BookingReminder.Kind.DAY_BEFORE,
    yc_id: str = "yc-stale",
    visit_offset_hours: float = 6.0,
    status: str = BookingReminder.Status.SENT_NO_REPLY,
    booking_request: BookingRequest | None = None,
) -> BookingReminder:
    """Helper — write a stale T-24h reminder by default.

    ``visit_offset_hours`` is *positive* offset from now to the visit.
    Default 6h means "visit is in 6 hours, escalation window applies".
    ``scheduled_at`` is computed as visit - 24h so the row shape
    matches what the R1 factory would produce.
    """
    now = timezone.now()
    visit_at = now + timedelta(hours=visit_offset_hours)
    scheduled_at = visit_at - timedelta(hours=24)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        booking_request=booking_request,
        yclients_record_id=yc_id,
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=kind,
        status=status,
        scheduled_at=scheduled_at,
        master_name="Lera",
        service_name="Массаж",
    )


# ────────────────────────────────────────────────────────────────────
# 1. Happy path
# ────────────────────────────────────────────────────────────────────
class TestHappyPath:
    def test_stale_t24h_escalates(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _make_reminder(tenant=tenant, bot_user=bot_user)
        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()

        assert result["escalated"] == 1
        assert result["no_manager"] == 0
        assert result["send_failed"] == 0

        row.refresh_from_db()
        assert row.status == BookingReminder.Status.ESCALATED

        assert mock_send.call_count == 1
        kwargs = mock_send.call_args.kwargs
        assert kwargs["chat_id"] == "mgr-chat-1"
        assert kwargs["attachments"] is None
        text = kwargs["text"]
        # Spec: message contains phone, name, service, visit time, master.
        assert "79991234567" in text
        assert "Anna" in text
        assert "Массаж" in text
        assert "Lera" in text
        # Visit time formatted — at least the HH:MM portion should appear.
        # We can't assert the exact strftime output (timezone, locale),
        # but the message must contain "Визит" header.
        assert "Визит" in text

    def test_message_contains_booking_request_id_when_linked(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        br = BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Lera",
            client_name="Anna",
            client_phone="79991234567",
            source="bot",
        )
        _make_reminder(tenant=tenant, bot_user=bot_user, booking_request=br)
        with patch("apps.bookings.escalation.send_message") as mock_send:
            escalate_stale_reminders()
        text = mock_send.call_args.kwargs["text"]
        assert str(br.id) in text

    def test_message_falls_back_to_reminder_id_when_no_booking_request(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        row = _make_reminder(tenant=tenant, bot_user=bot_user)
        with patch("apps.bookings.escalation.send_message") as mock_send:
            escalate_stale_reminders()
        text = mock_send.call_args.kwargs["text"]
        # No booking_request → fallback to reminder UUID for traceability.
        assert str(row.id) in text


# ────────────────────────────────────────────────────────────────────
# 2. Filter: TWO_HOURS ignored
# ────────────────────────────────────────────────────────────────────
class TestKindFilter:
    def test_two_hours_in_sent_no_reply_not_escalated(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # Hypothetical T-2h row with status SENT_NO_REPLY. In normal
        # operation T-2h transitions to SENT (terminal, no buttons),
        # but we assert defensively that even in a SENT_NO_REPLY state
        # the kind filter rejects it.
        row = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            kind=BookingReminder.Kind.TWO_HOURS,
            visit_offset_hours=1.0,
        )
        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()
        assert result["escalated"] == 0
        mock_send.assert_not_called()
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.SENT_NO_REPLY


# ────────────────────────────────────────────────────────────────────
# 3. Filter: status != SENT_NO_REPLY ignored
# ────────────────────────────────────────────────────────────────────
class TestStatusFilter:
    @pytest.mark.parametrize(
        "status",
        [
            BookingReminder.Status.PENDING,
            BookingReminder.Status.CONFIRMED,
            BookingReminder.Status.CANCELLED,
            BookingReminder.Status.RESCHEDULE_REQUESTED,
            BookingReminder.Status.ESCALATED,
            BookingReminder.Status.FAILED,
            BookingReminder.Status.SENT,
        ],
    )
    def test_non_sent_no_reply_status_skipped(
        self, tenant: Tenant, bot_user: BotUser, status: str
    ) -> None:
        row = _make_reminder(tenant=tenant, bot_user=bot_user, status=status)
        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()
        assert result["escalated"] == 0
        mock_send.assert_not_called()
        row.refresh_from_db()
        assert row.status == status  # unchanged


# ────────────────────────────────────────────────────────────────────
# 4. Filter: visit_at > now + 12h ignored
# ────────────────────────────────────────────────────────────────────
class TestVisitWindowFilter:
    def test_visit_too_far_in_future_not_escalated(self, tenant: Tenant, bot_user: BotUser) -> None:
        # Visit is 20h away — outside the 12h escalation window.
        row = _make_reminder(tenant=tenant, bot_user=bot_user, visit_offset_hours=20.0)
        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()
        assert result["escalated"] == 0
        mock_send.assert_not_called()
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.SENT_NO_REPLY

    def test_visit_exactly_at_window_boundary_escalates(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # visit_at exactly == now + 12h satisfies the ``<=`` predicate.
        # Slight 1-min buffer to absorb test runtime between now() in
        # the fixture and now() inside the task.
        row = _make_reminder(tenant=tenant, bot_user=bot_user, visit_offset_hours=11.9)
        with patch("apps.bookings.escalation.send_message"):
            result = escalate_stale_reminders()
        assert result["escalated"] == 1
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.ESCALATED

    def test_visit_already_passed_still_escalates(self, tenant: Tenant, bot_user: BotUser) -> None:
        # A row whose visit is already in the past (e.g. visit happened
        # at the salon but the client never tapped the button) is still
        # within ``visit_at <= now + 12h``, so it escalates. Operationally
        # this is correct: the manager calls to ask "did you make it?"
        # / "do you want to rebook?".
        row = _make_reminder(tenant=tenant, bot_user=bot_user, visit_offset_hours=-1.0)
        with patch("apps.bookings.escalation.send_message"):
            result = escalate_stale_reminders()
        assert result["escalated"] == 1
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.ESCALATED


# ────────────────────────────────────────────────────────────────────
# 5. Idempotency
# ────────────────────────────────────────────────────────────────────
class TestIdempotency:
    def test_second_run_is_noop(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(tenant=tenant, bot_user=bot_user)
        with patch("apps.bookings.escalation.send_message") as mock_send:
            first = escalate_stale_reminders()
            second = escalate_stale_reminders()
        assert first["escalated"] == 1
        assert second["escalated"] == 0
        assert second["no_manager"] == 0
        assert second["send_failed"] == 0
        # Only the first run dispatched a message.
        assert mock_send.call_count == 1


# ────────────────────────────────────────────────────────────────────
# 6. Multi-tenant fan-out
# ────────────────────────────────────────────────────────────────────
class TestMultiTenant:
    def test_each_tenant_gets_own_manager(self, db) -> None:
        t1 = Tenant.objects.create(slug="salon-a", name="Salon A", manager_chat_id="mgr-A")
        t2 = Tenant.objects.create(slug="salon-b", name="Salon B", manager_chat_id="mgr-B")
        bu1 = BotUser.all_tenants.create(
            tenant=t1,
            channel="max",
            channel_user_id="bu-a",
            chat_id="cli-A",
            phone="79990000001",
            client_name="Alice",
        )
        bu2 = BotUser.all_tenants.create(
            tenant=t2,
            channel="max",
            channel_user_id="bu-b",
            chat_id="cli-B",
            phone="79990000002",
            client_name="Bob",
        )
        _make_reminder(tenant=t1, bot_user=bu1, yc_id="yc-A")
        _make_reminder(tenant=t2, bot_user=bu2, yc_id="yc-B")

        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()

        assert result["escalated"] == 2
        assert mock_send.call_count == 2
        # Each call carried the correct per-tenant manager chat_id and
        # the correct per-tenant client phone number — no cross-leak.
        calls_by_chat = {
            call.kwargs["chat_id"]: call.kwargs["text"] for call in mock_send.call_args_list
        }
        assert set(calls_by_chat.keys()) == {"mgr-A", "mgr-B"}
        assert "79990000001" in calls_by_chat["mgr-A"]
        assert "Alice" in calls_by_chat["mgr-A"]
        assert "79990000002" in calls_by_chat["mgr-B"]
        assert "Bob" in calls_by_chat["mgr-B"]
        # Negative cross-checks — Alice's phone must NOT appear in
        # Salon B's manager message.
        assert "79990000001" not in calls_by_chat["mgr-B"]
        assert "Alice" not in calls_by_chat["mgr-B"]


# ────────────────────────────────────────────────────────────────────
# 7. Empty manager_chat_id — terminal flip + WARN + no send
# ────────────────────────────────────────────────────────────────────
class TestEmptyManagerChatId:
    def test_status_flips_but_no_send(
        self,
        tenant_no_manager: Tenant,
        bot_user_no_manager: BotUser,
        caplog,
    ) -> None:
        row = _make_reminder(tenant=tenant_no_manager, bot_user=bot_user_no_manager)
        import logging

        with (
            patch("apps.bookings.escalation.send_message") as mock_send,
            caplog.at_level(logging.WARNING, logger="apps.bookings.escalation"),
        ):
            result = escalate_stale_reminders()

        assert result["escalated"] == 0
        assert result["no_manager"] == 1
        assert mock_send.call_count == 0
        # Row still terminal — never re-tried.
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.ESCALATED
        # WARN logged for operator visibility.
        assert any("no_manager_chat" in rec.getMessage() for rec in caplog.records)

    def test_whitespace_only_manager_chat_id_treated_as_empty(self, db) -> None:
        t = Tenant.objects.create(slug="rem-esc-ws", name="WS Salon", manager_chat_id="   ")
        bu = BotUser.all_tenants.create(
            tenant=t,
            channel="max",
            channel_user_id="bu-ws",
            chat_id="c-ws",
            phone="79990000003",
            client_name="WS",
        )
        _make_reminder(tenant=t, bot_user=bu)
        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()
        assert result["no_manager"] == 1
        mock_send.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# 8. Send failure reverts status (at-least-once semantics)
# ────────────────────────────────────────────────────────────────────
class TestSendFailureReverts:
    def test_max_api_error_reverts_to_sent_no_reply(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        row = _make_reminder(tenant=tenant, bot_user=bot_user)
        with patch(
            "apps.bookings.escalation.send_message",
            side_effect=MaxAPIError(503, "service unavailable"),
        ):
            result = escalate_stale_reminders()
        assert result["send_failed"] == 1
        assert result["escalated"] == 0
        row.refresh_from_db()
        # Status reverted so next hourly tick retries.
        assert row.status == BookingReminder.Status.SENT_NO_REPLY

    def test_unexpected_exception_also_reverts(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _make_reminder(tenant=tenant, bot_user=bot_user)
        with patch(
            "apps.bookings.escalation.send_message",
            side_effect=RuntimeError("boom"),
        ):
            result = escalate_stale_reminders()
        assert result["send_failed"] == 1
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.SENT_NO_REPLY

    def test_next_tick_after_send_failure_retries(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _make_reminder(tenant=tenant, bot_user=bot_user)
        # First tick: send fails, status reverts.
        with patch(
            "apps.bookings.escalation.send_message",
            side_effect=MaxAPIError(502, "bad gateway"),
        ):
            escalate_stale_reminders()
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.SENT_NO_REPLY
        # Second tick: MAX recovers, escalation succeeds.
        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()
        assert result["escalated"] == 1
        assert mock_send.call_count == 1
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.ESCALATED


# ────────────────────────────────────────────────────────────────────
# Defensive — race condition + batch limit
# ────────────────────────────────────────────────────────────────────
class TestRaceCondition:
    def test_lost_race_skips_send(self, tenant: Tenant, bot_user: BotUser) -> None:
        """If CAS update returns rowcount=0, the worker skips."""
        _make_reminder(tenant=tenant, bot_user=bot_user)
        original_filter = BookingReminder.all_tenants.filter

        class FakeQS:
            def __init__(self, real):
                self._real = real

            def update(self, **kwargs):
                # The CAS update flips SENT_NO_REPLY → ESCALATED; mimic
                # "another worker beat us" by always returning 0.
                if (
                    "status" in kwargs
                    and len(kwargs) == 1
                    and kwargs["status"] == BookingReminder.Status.ESCALATED
                ):
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
            patch("apps.bookings.escalation.send_message") as mock_send,
        ):
            result = escalate_stale_reminders()

        assert mock_send.call_count == 0
        assert result["skipped"] >= 1
        assert result["escalated"] == 0


class TestBatchLimit:
    def test_batch_limit_enforced(self, tenant: Tenant, bot_user: BotUser) -> None:
        for i in range(7):
            _make_reminder(
                tenant=tenant,
                bot_user=bot_user,
                yc_id=f"yc-batch-{i}",
            )
        with (
            patch.object(escalation_mod, "BATCH_LIMIT", 3),
            patch("apps.bookings.escalation.send_message") as mock_send,
        ):
            result = escalate_stale_reminders()
        assert mock_send.call_count == 3
        assert result["escalated"] == 3


class TestEmptyQueue:
    def test_no_rows_returns_zero_counters(self, tenant: Tenant) -> None:
        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()
        assert result == {
            "escalated": 0,
            "no_manager": 0,
            "send_failed": 0,
            "skipped": 0,
        }
        mock_send.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# E0 #6 — Send-time booking-state recheck (founder verdict 2026-06-01)
# ────────────────────────────────────────────────────────────────────
class TestStateRecheckBeforeSend:
    """Escalation must not DM the manager for bookings that have been
    cancelled (or moved into other terminal states) after the T-24h
    reminder was sent. Mirrors the dispatch-side recheck in
    ``send_due_reminders`` so drop/defer semantics stay aligned."""

    def test_cancelled_booking_does_not_dm_manager(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        # Stale T-24h reminder linked to a booking the client just
        # cancelled through Ayla. Without E0 #6 the manager would
        # be DM'd "Клиент не подтвердил" — false positive that
        # damages the salon's relationship with the client.
        br = BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Lera",
            client_name="Anna",
            client_phone="79991234567",
            source="bot",
            status=BookingRequest.Status.CANCELLED,
        )
        row = _make_reminder(tenant=tenant, bot_user=bot_user, booking_request=br)

        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()

        mock_send.assert_not_called()
        assert result["skipped"] == 1
        assert result["escalated"] == 0

        # Row transitions to ESCALATED so it is not re-evaluated on
        # the next hourly tick — terminal state with a "dropped"
        # audit trail.
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.ESCALATED

    def test_completed_booking_does_not_dm_manager(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        # Booking was completed before the escalation window closed
        # (defensive — the 12h window normally bounds this out, but
        # T-24h reminders technically can race a same-day completion).
        br = BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Lera",
            client_name="Anna",
            client_phone="79991234567",
            source="bot",
            status=BookingRequest.Status.CONFIRMED,
            completed_at=timezone.now() - timedelta(minutes=10),
        )
        _make_reminder(tenant=tenant, bot_user=bot_user, booking_request=br)

        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()

        mock_send.assert_not_called()
        assert result["skipped"] == 1

    def test_cancel_requested_defers_without_terminal_transition(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        # Interim reversible state — client tapped Cancel but is still
        # within the ~5s undo window. The escalation should defer so
        # the next hourly tick can re-evaluate after the user commits.
        br = BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Lera",
            client_name="Anna",
            client_phone="79991234567",
            source="bot",
            status=BookingRequest.Status.CANCEL_REQUESTED,
        )
        row = _make_reminder(tenant=tenant, bot_user=bot_user, booking_request=br)

        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()

        mock_send.assert_not_called()
        assert result["skipped"] == 1
        # Row stays in SENT_NO_REPLY so next tick can re-decide.
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.SENT_NO_REPLY

    def test_confirmed_booking_still_escalates(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        # Sanity: a confirmed booking with no client reply on the
        # T-24h keyboard still escalates (the whole point of the
        # feature). Without this assertion an over-eager recheck
        # could silently swallow every escalation.
        br = BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Lera",
            client_name="Anna",
            client_phone="79991234567",
            source="bot",
            status=BookingRequest.Status.CONFIRMED,
        )
        _make_reminder(tenant=tenant, bot_user=bot_user, booking_request=br)

        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()

        assert mock_send.call_count == 1
        assert result["escalated"] == 1

    def test_null_booking_request_still_escalates(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        # NULL FK = legacy row OR Ayla-path (no BookingRequest mirror
        # yet). The helper returns SEND with reason
        # "null_fk_legacy_or_ayla_path" — documented Phase 0 gap.
        # Behaviour pinned so a future tightening of this branch is
        # an intentional change, not a regression.
        _make_reminder(tenant=tenant, bot_user=bot_user, booking_request=None)
        with patch("apps.bookings.escalation.send_message") as mock_send:
            result = escalate_stale_reminders()
        assert mock_send.call_count == 1
        assert result["escalated"] == 1
