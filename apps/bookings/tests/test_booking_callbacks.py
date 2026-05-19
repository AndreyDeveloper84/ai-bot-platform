"""Booking-gate callback tests (B5 / DRF-841).

Covers :class:`apps.bookings.callbacks.BookingGateCallbackSkill` — the
``cb:book:confirm:<token>`` / ``cb:book:cancel:<token>`` handlers
behind the 2-button preview gate.

Aspect coverage:

* ``matches()`` routing of the namespace.
* Each tap action across all three pending kinds (confirm / cancel
  / reschedule).
* TTL expiry — ✅ on a stale token returns the "too much time"
  reply.
* Double-tap idempotency — second click is a polite no-op.
* Cross-user authorisation — leaked token is rejected.
* YClients failure handoffs (cancel + reschedule).
* Reschedule partial-failure path — manager notified, user gets
  the polite handoff reply.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingRequest, PendingBookingAction
from apps.bookings.callbacks import (
    REPLY_BOOK_ALREADY_HANDLED,
    REPLY_BOOK_CANCEL_FAILED,
    REPLY_BOOK_CANCELLED_PREVIEW,
    REPLY_BOOK_EXPIRED,
    REPLY_BOOK_PARTIAL_FAILURE,
    REPLY_FORBIDDEN,
    REPLY_NOT_FOUND,
    BookingGateCallbackSkill,
)
from apps.bookings.pending_actions import create_pending
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.yclients import AvailableTime, BookingRecord, YClientsAPIError
from apps.skills.base import SkillContext
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="gate-cb", name="Gate CB", manager_chat_id="manager-1")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-g",
        chat_id="bu-g",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def other_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-other",
        chat_id="bu-other",
        phone="79990000000",
        client_name="Boris",
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    with tenant_scope(tenant):
        return Conversation.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            state=Conversation.State.IDLE,
        )


def _ctx(text: str, *, bot_user: BotUser, conversation: Conversation) -> SkillContext:
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        has_attachments=False,
    )


def _confirm_payload(slot_iso: str | None = None) -> dict[str, Any]:
    return {
        "master_id": 11,
        "service_id": 22,
        "slot_datetime": slot_iso or "2026-05-20T14:00:00",
        "client_phone": "79991234567",
        "client_name": "Anna",
        "master_name": "Ольга",
        "service_name": "Массаж",
    }


def _cancel_payload(record_id: int = 555) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "reason": "personal",
        "booking_request_id": "",
        "master_name": "Ольга",
        "service_name": "Массаж",
    }


def _reschedule_payload(record_id: int = 555, new_dt: str | None = None) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "new_datetime": new_dt or "2026-06-01T14:00:00",
        "master_id": 11,
        "service_id": 22,
        "master_name": "Ольга",
        "service_name": "Массаж",
        "client_phone": "79991234567",
        "client_name": "Anna",
        "booking_request_id": "",
    }


def _make_booking(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    yc_id: int = 555,
    status: str = BookingRequest.Status.CONFIRMED,
) -> BookingRequest:
    with tenant_scope(tenant):
        return BookingRequest.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Ольга",
            client_name="Anna",
            client_phone="79991234567",
            comment=f"Bot booking | yclients_record_id={yc_id}",
            source="bot",
            status=status,
        )


class FakeYClients:
    """Minimal stub — only the methods the gate executor calls."""

    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.cancel_calls: list[int] = []
        self.create_response: BookingRecord | None = None
        self.create_exc: Exception | None = None
        self.cancel_exc: Exception | None = None
        # Skills retro hotfix #4: execute_reschedule re-checks slot
        # availability at execute time. Default = the requested slot is
        # still free (matches `time`/`datetime` via `_slot_available`'s
        # `if t.time` and `if t.datetime` branches with explicit None →
        # which would short-circuit). We expose a list of AvailableTime
        # rows; tests covering the "slot taken" race must leave this
        # list empty.
        self.times: list[AvailableTime] = []
        self.times_exc: Exception | None = None

    def create_record(self, **kwargs):
        self.create_calls.append(kwargs)
        if self.create_exc is not None:
            raise self.create_exc
        return self.create_response or BookingRecord(record_id=777, record_hash="h", raw={})

    def cancel_record(self, *, record_id: int) -> bool:
        self.cancel_calls.append(record_id)
        if self.cancel_exc is not None:
            raise self.cancel_exc
        return True

    def get_available_times(self, **_: Any) -> list[AvailableTime]:
        if self.times_exc is not None:
            raise self.times_exc
        return list(self.times)


def _patch_yclients(client: FakeYClients):
    return patch("apps.integrations.yclients.get_yclients_client", return_value=client)


# ---------------------------------------------------------------------------
# matches()
# ---------------------------------------------------------------------------


class TestMatches:
    def test_matches_confirm_namespace(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx("cb:book:confirm:abc", bot_user=bot_user, conversation=conversation)
        assert BookingGateCallbackSkill().matches(ctx)

    def test_matches_cancel_namespace(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx("cb:book:cancel:abc", bot_user=bot_user, conversation=conversation)
        assert BookingGateCallbackSkill().matches(ctx)

    def test_does_not_match_reminder_namespace(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx("cb:rem:confirm:abc", bot_user=bot_user, conversation=conversation)
        assert not BookingGateCallbackSkill().matches(ctx)

    def test_does_not_match_plain_text(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx("привет", bot_user=bot_user, conversation=conversation)
        assert not BookingGateCallbackSkill().matches(ctx)


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


class TestMalformed:
    def test_bad_uuid(self, tenant: Tenant, bot_user: BotUser, conversation: Conversation) -> None:
        ctx = _ctx(
            "cb:book:confirm:not-a-uuid",
            bot_user=bot_user,
            conversation=conversation,
        )
        result = BookingGateCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_NOT_FOUND

    def test_unknown_token_returns_not_found(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        import uuid

        ctx = _ctx(
            f"cb:book:confirm:{uuid.uuid4()}",
            bot_user=bot_user,
            conversation=conversation,
        )
        result = BookingGateCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_NOT_FOUND


# ---------------------------------------------------------------------------
# Confirm-tap: confirm flow
# ---------------------------------------------------------------------------


class TestConfirmTapConfirm:
    def test_happy_path_creates_record(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        future_iso = (timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(future_iso),
        )
        client = FakeYClients()
        client.create_response = BookingRecord(record_id=12345, record_hash="h", raw={})
        ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with _patch_yclients(client):
            result = BookingGateCallbackSkill().handle(ctx)
        assert result.should_handoff is False
        assert client.create_calls
        rows = BookingRequest.all_tenants.filter(comment__contains="yclients_record_id=12345")
        assert rows.count() == 1
        # Token consumed.
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.consumed_at is not None

    def test_double_tap_idempotent(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        future_iso = (timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(future_iso),
        )
        client = FakeYClients()
        client.create_response = BookingRecord(record_id=12345, record_hash="h", raw={})
        ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with _patch_yclients(client):
            first = BookingGateCallbackSkill().handle(ctx)
            second = BookingGateCallbackSkill().handle(ctx)
        assert first.should_handoff is False
        assert second.reply_text == REPLY_BOOK_ALREADY_HANDLED
        # Only ONE create call.
        assert len(client.create_calls) == 1


# ---------------------------------------------------------------------------
# Confirm-tap: expired
# ---------------------------------------------------------------------------


class TestExpired:
    def test_expired_returns_polite_reply(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(),
        )
        # Force expiry.
        PendingBookingAction.all_tenants.filter(pk=token).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        client = FakeYClients()
        ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with _patch_yclients(client):
            result = BookingGateCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_BOOK_EXPIRED
        # No YClients call on expired token.
        assert client.create_calls == []


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


class TestAuthorisation:
    def test_other_user_cannot_consume_token_owner_can(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        other_user: BotUser,
        conversation: Conversation,
    ) -> None:
        """Foreign tap is rejected BEFORE the consume-CAS fires.

        Without ordering authorisation before consume, a leaked-token
        attack would let any user race-claim the row, locking the
        legitimate owner out of their own confirmation. This test
        nails down the ordering by exercising both taps.
        """
        future_iso = (timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(future_iso),
        )
        client = FakeYClients()
        client.create_response = BookingRecord(record_id=42, record_hash="h", raw={})

        # Foreign user taps — denied.
        foreign_ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=other_user,
            conversation=conversation,
        )
        with _patch_yclients(client):
            foreign_result = BookingGateCallbackSkill().handle(foreign_ctx)
        assert foreign_result.reply_text == REPLY_FORBIDDEN
        assert client.create_calls == []
        # Row NOT consumed — owner still has a clean slate.
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.consumed_at is None

        # Owner taps — succeeds.
        owner_ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with _patch_yclients(client):
            owner_result = BookingGateCallbackSkill().handle(owner_ctx)
        assert owner_result.should_handoff is False
        assert client.create_calls  # YClients record actually created


# ---------------------------------------------------------------------------
# Cancel-tap path (❌ button)
# ---------------------------------------------------------------------------


class TestCancelTap:
    def test_cancel_tap_discards_pending(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(),
        )
        client = FakeYClients()
        ctx = _ctx(
            f"cb:book:cancel:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with _patch_yclients(client):
            result = BookingGateCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_BOOK_CANCELLED_PREVIEW
        # No destructive call.
        assert client.create_calls == []
        # Row consumed (so a subsequent confirm-tap returns "already").
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.consumed_at is not None

    def test_cancel_tap_forbidden_for_other_user(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        other_user: BotUser,
        conversation: Conversation,
    ) -> None:
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(),
        )
        ctx = _ctx(
            f"cb:book:cancel:{token}",
            bot_user=other_user,
            conversation=conversation,
        )
        result = BookingGateCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_FORBIDDEN
        # Not consumed — legitimate owner can still cancel later.
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.consumed_at is None


# ---------------------------------------------------------------------------
# Confirm-tap: cancel flow
# ---------------------------------------------------------------------------


class TestConfirmTapCancel:
    def test_happy_path_cancels(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CANCEL,
            payload=_cancel_payload(),
        )
        client = FakeYClients()
        ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with _patch_yclients(client):
            result = BookingGateCallbackSkill().handle(ctx)
        assert result.should_handoff is False
        assert client.cancel_calls == [555]
        booking = BookingRequest.all_tenants.get(comment__contains="yclients_record_id=555")
        assert booking.status == BookingRequest.Status.CANCELLED

    def test_yclients_failure_handoff(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CANCEL,
            payload=_cancel_payload(),
        )
        client = FakeYClients()
        client.cancel_exc = YClientsAPIError("http_404")
        ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with _patch_yclients(client):
            result = BookingGateCallbackSkill().handle(ctx)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_cancel_yclients_failure"
        assert result.reply_text == REPLY_BOOK_CANCEL_FAILED


# ---------------------------------------------------------------------------
# Confirm-tap: reschedule flow
# ---------------------------------------------------------------------------


class TestConfirmTapReschedule:
    def test_happy_path_notifies_manager(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.RESCHEDULE,
            payload=_reschedule_payload(),
        )
        client = FakeYClients()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        # Slot still free at execute time — passes the hotfix #4 recheck.
        client.times = [
            AvailableTime(time="14:00", datetime="2026-06-01T14:00:00", seance_length_s=3600)
        ]
        ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with (
            _patch_yclients(client),
            patch("apps.channels.max.outbound.send_message") as send_mock,
        ):
            result = BookingGateCallbackSkill().handle(ctx)
        assert result.should_handoff is False
        # Manager notified about the reschedule.
        assert send_mock.called
        assert send_mock.call_args.kwargs["chat_id"] == "manager-1"

    def test_partial_failure_notifies_manager_and_handoffs(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.RESCHEDULE,
            payload=_reschedule_payload(),
        )
        client = FakeYClients()
        client.create_exc = YClientsAPIError("http_400")
        # Recheck passes; failure happens later at create_record (partial-
        # failure path covered by this test).
        client.times = [
            AvailableTime(time="14:00", datetime="2026-06-01T14:00:00", seance_length_s=3600)
        ]
        ctx = _ctx(
            f"cb:book:confirm:{token}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with (
            _patch_yclients(client),
            patch("apps.channels.max.outbound.send_message") as send_mock,
        ):
            result = BookingGateCallbackSkill().handle(ctx)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_reschedule_partial_failure"
        assert result.reply_text == REPLY_BOOK_PARTIAL_FAILURE
        # Manager notified — informational text contains the warning emoji.
        assert send_mock.called
        body = send_mock.call_args.kwargs["text"]
        assert "не завершён" in body or "Перенос" in body
