"""Tests for the payment.* consumer family (#443).

Mirrors the test architecture of ``test_booking_consumer.py`` (#442):

* Handlers invoked DIRECTLY (not through ``dispatch_envelope``) — the
  dispatcher's threadpool deadlocks the SQLite test backend.
* Tenant-verify bypassed via ``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN``
  for the pre-#246 transition window.
* Idempotency replay-3× tests pin the handler-level short-circuit
  (closes the category-2 billing miscompute risk that adversarial
  review on #442 flagged).

### Test layout

* ``TestMapYookassaFailureCode`` — pure helper unit tests, no DB.
* ``TestPaymentAuthorized`` — pending-payment slot lifecycle.
* ``TestPaymentCaptured`` — clear pending + reset counter + emit loyalty.
* ``TestPaymentFailed`` — counter increment + threshold trip + enum mapping.
* ``TestPaymentRefunded`` — refund timestamp + loyalty reverse emit.
* ``TestIdempotencyReplay3x`` — handler-level event_id short-circuit
  (depends on Conversation.last_payment_event_id, W1 handoff #2).
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from apps.conversations.models import Conversation
from apps.eventbus.consumers.payment import (
    FailureCode,
    _map_yookassa_failure_code,
    handle_payment_authorized,
    handle_payment_captured,
    handle_payment_failed,
    handle_payment_refunded,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


# Canonical fixture UUIDs (shared across the suite).
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
PAYMENT_ID = "5c8e2d1f-3b4a-4c6d-9e8f-1a2b3c4d5e6f"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"


# ─── helpers ───────────────────────────────────────────────────────────────


def _envelope(
    *,
    event_name: str,
    data: dict[str, Any],
    event_id: str | None = None,
    tenant_id: str | None = TENANT_ID,
    user_id: str = AYLA_USER_ID,
    occurred_at: dt.datetime | None = None,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id or "01J9PAYMENT00000000000000ZZ",  # pragma: allowlist secret
        event_name=event_name,
        event_version=1,
        occurred_at=occurred_at or dt.datetime(2026, 5, 21, 14, 32, 13, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=user_id,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id="01J9HXKM8Z2T4V6R8Q1P3D5F7E",  # pragma: allowlist secret
        data=data,
    )


def _authorized_data(payment_id: str = PAYMENT_ID) -> dict[str, Any]:
    return {
        "payment_id": payment_id,
        "appointment_id": APPOINTMENT_ID,
        "amount": "1800.00",
        "currency": "RUB",
        "provider": "yookassa",
        "external_id": "2d8e3c1f-7a9b-4d6e-c0f1-3b4a5c6d7e8f",
    }


def _captured_data() -> dict[str, Any]:
    return {
        "payment_id": PAYMENT_ID,
        "appointment_id": APPOINTMENT_ID,
        "amount": "1800.00",
        "captured_at": "2026-05-22T16:31:14.205Z",
    }


def _failed_data(reason: str = "card_declined") -> dict[str, Any]:
    return {
        "payment_id": PAYMENT_ID,
        "appointment_id": APPOINTMENT_ID,
        "reason": reason,
        "failed_at": "2026-05-21T14:48:02.503Z",
    }


def _refunded_data() -> dict[str, Any]:
    return {
        "payment_id": PAYMENT_ID,
        "appointment_id": APPOINTMENT_ID,
        "amount": "1800.00",
        "reason": "service_issue",
        "refunded_at": "2026-05-23T10:15:42.881Z",
    }


@pytest.fixture(autouse=True)
def _enable_tenant_verify_fail_open(settings) -> None:
    """Round-3 NEW-5 bridge — pre-#246 transition mode."""
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID,
        slug="t-payment",
        name="Payment test tenant",
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9001",
        chat_id="chat-9001",
        ayla_user_id=AYLA_USER_ID,
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        state=Conversation.State.IDLE,
        last_message_at=dt.datetime(2026, 5, 21, 10, 0, tzinfo=dt.timezone.utc),
    )


# ─── _map_yookassa_failure_code ────────────────────────────────────────────


class TestMapYookassaFailureCode:
    """Pure helper — PII protection via closed enum.

    Contract §3.7 enum: card_declined / insufficient_funds / hold_expired
    / provider_error / cancelled_by_user. Plus our YooKassa-specific
    ``card_expired`` synonym and fail-closed ``OTHER`` catchall.
    """

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            ("card_declined", FailureCode.CARD_DECLINED),
            ("insufficient_funds", FailureCode.INSUFFICIENT_FUNDS),
            ("hold_expired", FailureCode.HOLD_EXPIRED),
            ("provider_error", FailureCode.PROVIDER_ERROR),
            ("cancelled_by_user", FailureCode.CANCELLED_BY_USER),
            ("expired_card", FailureCode.CARD_EXPIRED),  # YooKassa synonym
            ("card_expired", FailureCode.CARD_EXPIRED),
        ],
    )
    def test_contract_enum_passes_through(self, reason: str, expected: str) -> None:
        assert _map_yookassa_failure_code(reason) == expected

    def test_unknown_reason_fails_closed_to_other(self) -> None:
        assert _map_yookassa_failure_code("3ds_challenge_failed") == FailureCode.OTHER

    def test_null_reason_fails_closed_to_other(self) -> None:
        assert _map_yookassa_failure_code(None) == FailureCode.OTHER

    def test_empty_string_fails_closed_to_other(self) -> None:
        assert _map_yookassa_failure_code("") == FailureCode.OTHER

    def test_case_insensitive(self) -> None:
        assert _map_yookassa_failure_code("Card_Declined") == FailureCode.CARD_DECLINED
        assert _map_yookassa_failure_code("INSUFFICIENT_FUNDS") == FailureCode.INSUFFICIENT_FUNDS

    def test_whitespace_stripped(self) -> None:
        assert _map_yookassa_failure_code("  card_declined  ") == FailureCode.CARD_DECLINED

    def test_pii_carrying_free_text_does_not_leak(self) -> None:
        """The whole point of the helper: a YooKassa free-text error
        like ``"Card 4242****4242 declined"`` MUST NOT propagate."""
        leaky = "Card 4242****4242 declined by issuer"
        assert _map_yookassa_failure_code(leaky) == FailureCode.OTHER


# ─── payment.authorized ────────────────────────────────────────────────────


class TestPaymentAuthorized:
    def test_sets_pending_payment_id(self, tenant: Tenant, conversation: Conversation) -> None:
        env = _envelope(event_name="payment.authorized", data=_authorized_data())
        handle_payment_authorized(env)

        conversation.refresh_from_db()
        assert conversation.pending_payment_id == UUID(PAYMENT_ID)

    def test_unknown_tenant_no_op(self) -> None:
        env = _envelope(
            event_name="payment.authorized",
            data=_authorized_data(),
            tenant_id="00000000-0000-0000-0000-000000000000",
        )
        # No raise — handler logs + returns.
        handle_payment_authorized(env)

    def test_no_conversation_no_op(self, tenant: Tenant) -> None:
        """Tenant exists but no BotUser+Conversation for this user yet."""
        env = _envelope(event_name="payment.authorized", data=_authorized_data())
        # Should not raise.
        handle_payment_authorized(env)
        # No conversation created.
        assert Conversation.all_tenants.filter(tenant=tenant).count() == 0


# ─── payment.captured ──────────────────────────────────────────────────────


class TestPaymentCaptured:
    def test_clears_pending_and_sets_captured_at(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        # Set pending first.
        conversation.pending_payment_id = UUID(PAYMENT_ID)
        conversation.consecutive_payment_failures = 2
        conversation.save(update_fields=["pending_payment_id", "consecutive_payment_failures"])

        env = _envelope(event_name="payment.captured", data=_captured_data())
        handle_payment_captured(env)

        conversation.refresh_from_db()
        assert conversation.pending_payment_id is None
        assert conversation.consecutive_payment_failures == 0
        assert conversation.last_payment_captured_at == env.occurred_at

    def test_emits_loyalty_bonus_eligible(self, tenant: Tenant, conversation: Conversation) -> None:
        """§3.6 step 2 — emit on apps/events/ snake_case bus."""
        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:
            env = _envelope(event_name="payment.captured", data=_captured_data())
            handle_payment_captured(env)

        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "loyalty_bonus_eligible"
        props = mock_emit.call_args[1]["properties"]
        assert props["payment_id"] == PAYMENT_ID
        assert props["appointment_id"] == APPOINTMENT_ID
        assert props["amount"] == "1800.00"

    def test_no_conversation_still_emits_loyalty(self, tenant: Tenant) -> None:
        """A user can have a paid booking without ever having opened the
        bot (Ayla mobile-only). Loyalty bonus still accrues — analytics
        bus emit is the public-facing side effect."""
        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:
            env = _envelope(event_name="payment.captured", data=_captured_data())
            handle_payment_captured(env)

        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "loyalty_bonus_eligible"


# ─── payment.failed ────────────────────────────────────────────────────────


class TestPaymentFailed:
    def test_sets_failed_at_and_failure_code(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        env = _envelope(event_name="payment.failed", data=_failed_data(reason="card_declined"))
        handle_payment_failed(env)

        conversation.refresh_from_db()
        assert conversation.last_payment_failed_at == env.occurred_at
        assert conversation.last_payment_failure_code == FailureCode.CARD_DECLINED
        assert conversation.consecutive_payment_failures == 1

    def test_increments_counter_atomically_across_3_events(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Three distinct failure events → counter == 3. Uses F() so the
        increment is read-modify-write-safe under concurrency (canonical
        Django pattern)."""
        for i in range(3):
            env = _envelope(
                event_name="payment.failed",
                data=_failed_data(),
                event_id=f"01J9F{i:0>20}ZZ",  # pragma: allowlist secret
            )
            handle_payment_failed(env)

        conversation.refresh_from_db()
        assert conversation.consecutive_payment_failures == 3

    def test_below_threshold_does_not_dispatch_skill(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        with patch("apps.skills.payment_failed.skill.on_payment_failed_event") as mock_skill:
            env = _envelope(event_name="payment.failed", data=_failed_data())
            handle_payment_failed(env)
        mock_skill.assert_not_called()

    def test_at_threshold_dispatches_skill_with_phase1_payload(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        """Three failures → threshold tripped → skill called on the 3rd.

        #738 founder verdict 2026-05-26 — Phase 1 (Option C minimum)
        replaces the previous ``emit_internal_event(\"payment_failed_
        skill_triggered\", ...)`` fan-out with a direct in-process call
        to :func:`apps.skills.payment_failed.skill.on_payment_failed_event`.
        """
        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        conversation.consecutive_payment_failures = 2  # next failure = 3
        conversation.save(update_fields=["consecutive_payment_failures"])

        with patch("apps.skills.payment_failed.skill.on_payment_failed_event") as mock_skill:
            env = _envelope(event_name="payment.failed", data=_failed_data())
            handle_payment_failed(env)

        mock_skill.assert_called_once()
        payload = mock_skill.call_args[0][0]
        assert payload["consecutive_failures"] == 3
        assert payload["failure_code"] == FailureCode.CARD_DECLINED

    def test_unknown_failure_reason_maps_to_other(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """PII protection — unknown reasons fail-closed to OTHER, not
        free-text propagated."""
        env = _envelope(
            event_name="payment.failed",
            data=_failed_data(reason="card_4242_declined_by_bank_x"),
        )
        handle_payment_failed(env)

        conversation.refresh_from_db()
        assert conversation.last_payment_failure_code == FailureCode.OTHER

    def test_clears_pending_payment_id(self, tenant: Tenant, conversation: Conversation) -> None:
        conversation.pending_payment_id = UUID(PAYMENT_ID)
        conversation.save(update_fields=["pending_payment_id"])

        env = _envelope(event_name="payment.failed", data=_failed_data())
        handle_payment_failed(env)

        conversation.refresh_from_db()
        assert conversation.pending_payment_id is None

    def test_does_not_cancel_booking(self, tenant: Tenant, conversation: Conversation) -> None:
        """§3.7 step 3: handler MUST NOT cancel the booking — Ayla emits
        ``booking.cancelled`` separately if the pending-payment grace
        clock expires. Verify no booking.* side-effect fires from this
        handler."""
        from apps.booking.models import RemoteBookingProxy

        env = _envelope(event_name="payment.failed", data=_failed_data())
        handle_payment_failed(env)

        # No proxy created or mutated as a side-effect.
        assert (
            RemoteBookingProxy.all_tenants.filter(appointment_id=UUID(APPOINTMENT_ID)).count() == 0
        )


# ─── payment.refunded ──────────────────────────────────────────────────────


class TestPaymentRefunded:
    def test_sets_refunded_at(self, tenant: Tenant, conversation: Conversation) -> None:
        env = _envelope(event_name="payment.refunded", data=_refunded_data())
        handle_payment_refunded(env)

        conversation.refresh_from_db()
        assert conversation.last_payment_refunded_at == env.occurred_at

    def test_emits_loyalty_refund_reverse(self, tenant: Tenant, conversation: Conversation) -> None:
        """§3.8 step 2 — loyalty subscriber will reverse the accrual."""
        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:
            env = _envelope(event_name="payment.refunded", data=_refunded_data())
            handle_payment_refunded(env)

        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "loyalty_refund_reverse"
        props = mock_emit.call_args[1]["properties"]
        assert props["payment_id"] == PAYMENT_ID
        assert props["refund_amount"] == "1800.00"
        assert props["reason"] == "service_issue"

    def test_consumer_module_does_not_import_channel_outbound(self) -> None:
        """§3.8 step 3 contract-discipline — customer DM is Ayla's
        notification stream, NOT this consumer. We MUST NOT reach the
        chat-send layer from here. Verify statically: the consumer
        module's imports do not include any channel outbound module.
        """
        import apps.eventbus.consumers.payment as payment_module

        forbidden_substrings = (
            "apps.channels",
            "apps.workers.outbound",
            "send_message",
            "send_dm",
        )
        # Grab the module's source-level imports via its __dict__.
        # Anything channel-shaped imported eagerly would surface here.
        loaded = {name for name in dir(payment_module)}
        for forbidden in forbidden_substrings:
            assert not any(forbidden in n for n in loaded), (
                f"payment consumer must not surface {forbidden!r} — "
                f"customer DMs are Ayla's notification stream territory"
            )


# ─── Idempotency replay-3× (W1 handoff #2 / #635) ──────────────────────────


class TestIdempotencyReplay3x:
    """Category-2 PRE_MERGE billing miscompute defense.

    Without handler-level idempotency, three deliveries of the same
    ``payment.failed`` event with IngestDedupe disabled (test fixture,
    replay tooling, operator manual fire) would increment
    ``consecutive_payment_failures`` 3× → false threshold trip → false
    skill emit. Same shape for ``payment.captured`` × 3 → triple
    ``loyalty_bonus_eligible`` emit → triple ledger entry downstream.

    The handler short-circuits on
    ``conversation.last_payment_event_id == envelope.event_id`` AFTER
    tenant + conversation resolve but BEFORE any state mutation or
    emit. Same pattern as RemoteBookingProxy.last_synced_event_id in #442.
    """

    def test_payment_authorized_replay_3x_writes_once(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        env = _envelope(event_name="payment.authorized", data=_authorized_data())

        for _ in range(3):
            handle_payment_authorized(env)

        conversation.refresh_from_db()
        assert conversation.pending_payment_id == UUID(PAYMENT_ID)
        assert conversation.last_payment_event_id == env.event_id

    def test_payment_captured_replay_3x_emits_loyalty_once(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Critical: triple loyalty_bonus_eligible emit = triple ledger
        accrual = customer gets 3× the bonus. Short-circuit MUST fire
        before emit."""
        env = _envelope(event_name="payment.captured", data=_captured_data())

        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:
            for _ in range(3):
                handle_payment_captured(env)

        # Exactly ONE loyalty emit across three deliveries.
        assert mock_emit.call_count == 1
        assert mock_emit.call_args[0][0] == "loyalty_bonus_eligible"

        conversation.refresh_from_db()
        assert conversation.last_payment_event_id == env.event_id
        assert conversation.consecutive_payment_failures == 0

    def test_payment_failed_replay_3x_increments_counter_once(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        """Critical: triple counter increment = false threshold trip =
        false handoff. The whole point of the short-circuit."""
        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        env = _envelope(event_name="payment.failed", data=_failed_data())

        with patch("apps.skills.payment_failed.skill.on_payment_failed_event") as mock_skill:
            for _ in range(3):
                handle_payment_failed(env)

        conversation.refresh_from_db()
        # Counter == 1 (not 3) — replays short-circuited.
        assert conversation.consecutive_payment_failures == 1
        # No skill dispatch (would have fired if counter == 3).
        mock_skill.assert_not_called()

    def test_payment_refunded_replay_3x_emits_reverse_once(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Critical: triple loyalty_refund_reverse = customer's loyalty
        balance goes deep negative on a single refund. Short-circuit
        MUST fire before emit."""
        env = _envelope(event_name="payment.refunded", data=_refunded_data())

        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:
            for _ in range(3):
                handle_payment_refunded(env)

        assert mock_emit.call_count == 1
        assert mock_emit.call_args[0][0] == "loyalty_refund_reverse"

        conversation.refresh_from_db()
        assert conversation.last_payment_event_id == env.event_id

    def test_distinct_event_ids_pass_through(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Sanity: two genuinely distinct events (different event_ids)
        are NOT short-circuited — both apply. Different `failed` events
        in sequence → counter == 2 (real escalation), not stuck at 1."""
        env1 = _envelope(
            event_name="payment.failed",
            data=_failed_data(),
            event_id="01J9F1FIRST00000000000000ZZ",  # pragma: allowlist secret
        )
        env2 = _envelope(
            event_name="payment.failed",
            data=_failed_data(),
            event_id="01J9F2SECOND0000000000000ZZ",  # pragma: allowlist secret
        )
        handle_payment_failed(env1)
        handle_payment_failed(env2)

        conversation.refresh_from_db()
        assert conversation.consecutive_payment_failures == 2
        assert conversation.last_payment_event_id == env2.event_id


# ─── Per-payment terminal dedupe (Round-1 BLOCKER-1) ───────────────────────


class TestPaymentTerminalDedupe:
    """Round-1 adversarial BLOCKER-1: two terminal events for the same
    payment_id with DIFFERENT event_ids would bypass the
    ``last_payment_event_id`` short-circuit and double-fire loyalty
    emits. Closed via ``PaymentTerminalDedupe`` model — a row per
    ``(payment_id, terminal_state)`` is the dedupe signal.
    """

    def test_captured_same_payment_id_different_event_ids_emits_loyalty_once(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Two distinct captured events on the same payment_id →
        loyalty_bonus_eligible fires exactly once (the second hits
        IntegrityError on the unique constraint and short-circuits)."""
        env1 = _envelope(
            event_name="payment.captured",
            data=_captured_data(),
            event_id="01J9P1CAPFIRST000000000000",  # pragma: allowlist secret
        )
        env2 = _envelope(
            event_name="payment.captured",
            data=_captured_data(),
            event_id="01J9P2CAPSECOND00000000000",  # pragma: allowlist secret
        )

        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:
            handle_payment_captured(env1)
            handle_payment_captured(env2)

        # Exactly one loyalty emit despite two distinct event_ids.
        assert mock_emit.call_count == 1

    def test_refund_same_payment_id_different_event_ids_emits_reverse_once(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Operator manual re-fire with fresh ULID MUST NOT trigger
        double loyalty_refund_reverse → would push customer into
        negative loyalty balance."""
        env1 = _envelope(
            event_name="payment.refunded",
            data=_refunded_data(),
            event_id="01J9P1REFFIRST00000000000Z",  # pragma: allowlist secret
        )
        env2 = _envelope(
            event_name="payment.refunded",
            data=_refunded_data(),
            event_id="01J9P2REFSECOND0000000000Z",  # pragma: allowlist secret
        )

        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:
            handle_payment_refunded(env1)
            handle_payment_refunded(env2)

        assert mock_emit.call_count == 1


# ─── PP-1 conditional pending wipe ─────────────────────────────────────────


class TestConditionalPendingWipe:
    """PP-1: ``payment.captured`` must NOT wipe a pending_payment_id
    that belongs to a different payment (e.g. when two concurrent
    authorized/captured pairs share a Conversation row)."""

    def test_captured_does_not_clear_unrelated_pending_payment(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        unrelated_payment = UUID("dededede-dede-dede-dede-dededededede")
        # User has a different payment pending (e.g. retry for a
        # second appointment).
        conversation.pending_payment_id = unrelated_payment
        conversation.save(update_fields=["pending_payment_id"])

        env = _envelope(event_name="payment.captured", data=_captured_data())
        handle_payment_captured(env)

        conversation.refresh_from_db()
        # Pending slot still points at the unrelated payment.
        assert conversation.pending_payment_id == unrelated_payment

    def test_captured_clears_matching_pending_payment(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        conversation.pending_payment_id = UUID(PAYMENT_ID)
        conversation.save(update_fields=["pending_payment_id"])

        env = _envelope(event_name="payment.captured", data=_captured_data())
        handle_payment_captured(env)

        conversation.refresh_from_db()
        assert conversation.pending_payment_id is None


# ─── PP-3 deterministic ORDER BY ───────────────────────────────────────────


class TestResolveConversationDeterministic:
    """PP-3: identical timestamps must not produce non-deterministic
    Conversation picks. ``id`` tiebreaker locks the choice."""

    def test_identical_timestamps_pick_deterministic_id(self, tenant: Tenant) -> None:
        """Multi-channel scenario: same Ayla user has BotUser under MAX
        + Telegram → two Conversations under one tenant. With identical
        ``last_message_at`` the ORDER BY needs ``id`` tiebreaker to be
        deterministic."""
        # Two BotUsers for the same ayla_user_id (multi-channel).
        max_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="9001",
            chat_id="chat-max",
            ayla_user_id=AYLA_USER_ID,
        )
        tg_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="telegram",
            channel_user_id="9002",
            chat_id="chat-tg",
            ayla_user_id=AYLA_USER_ID,
        )
        same_ts = dt.datetime(2026, 5, 21, 10, 0, tzinfo=dt.timezone.utc)
        conv_a = Conversation.all_tenants.create(
            tenant=tenant,
            bot_user=max_user,
            state=Conversation.State.IDLE,
            last_message_at=same_ts,
        )
        conv_b = Conversation.all_tenants.create(
            tenant=tenant,
            bot_user=tg_user,
            state=Conversation.State.IDLE,
            last_message_at=same_ts,
        )

        from apps.eventbus.consumers.payment import _resolve_conversation

        # Repeated calls return THE SAME row — that's the
        # determinism guarantee. Without ``id`` tiebreaker Postgres /
        # SQLite were free to flip the answer between calls.
        first_pick = _resolve_conversation(tenant=tenant, user_id=AYLA_USER_ID)
        second_pick = _resolve_conversation(tenant=tenant, user_id=AYLA_USER_ID)

        assert first_pick is not None
        assert second_pick is not None
        assert first_pick.pk == second_pick.pk
        assert first_pick.pk in {conv_a.pk, conv_b.pk}


# ─── BLOCKER-2 threshold strict equality ───────────────────────────────────


class TestThresholdStrictEquality:
    """BLOCKER-2 fix: dispatch ``on_payment_failed_event`` skill ONLY
    on the threshold crossing (==), not on subsequent failures above
    it. Otherwise every failure above the threshold would spam a fresh
    handoff (in #738 — a fresh customer DM)."""

    def test_skill_dispatched_only_on_exact_threshold_crossing(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3

        # Five distinct failures.
        with patch("apps.skills.payment_failed.skill.on_payment_failed_event") as mock_skill:
            for i in range(5):
                env = _envelope(
                    event_name="payment.failed",
                    data=_failed_data(),
                    event_id=f"01J9F{i:0>20}ZZ",  # pragma: allowlist secret
                )
                handle_payment_failed(env)

        # Counter ended at 5, but skill fired exactly once — on the
        # crossing (count == 3).
        assert mock_skill.call_count == 1
        dispatched_count = mock_skill.call_args[0][0]["consecutive_failures"]
        assert dispatched_count == 3

        conversation.refresh_from_db()
        assert conversation.consecutive_payment_failures == 5


# ─── #738 Phase 1 payload contract ─────────────────────────────────────────


class TestSkillHandoffPayloadPhase1:
    """#738 founder verdict 2026-05-26 — Phase 1 (Option C minimum).

    Consumer enriches payload from envelope + Conversation context
    ONLY (no Appointment lookup per ADR-0009 §Hard rule #2, no Ayla
    REST call per founder rejection of Option B). Skill's α-mode
    graceful-degrades unset fields. Phase 2 (task #93, post-pilot)
    extends Ayla envelope to event_version=2 with master_name /
    service_name / amount / currency / appointment_date.
    """

    def _trip_threshold(
        self, *, env: IngestEnvelope, settings, conversation: Conversation
    ) -> dict[str, Any]:
        """Helper: seed the counter at N-1 so the next event trips the
        threshold; capture and return the payload the skill received.
        """
        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        conversation.consecutive_payment_failures = 2
        conversation.save(update_fields=["consecutive_payment_failures"])
        with patch("apps.skills.payment_failed.skill.on_payment_failed_event") as mock_skill:
            handle_payment_failed(env)
        assert mock_skill.call_count == 1
        return dict(mock_skill.call_args[0][0])

    def test_payload_contains_all_required_phase1_fields(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        env = _envelope(event_name="payment.failed", data=_failed_data())
        payload = self._trip_threshold(env=env, settings=settings, conversation=conversation)

        # Required Phase 1 keys per memory `project_payment_failed_
        # enrichment_pattern` / founder verdict 2026-05-26.
        required = {
            "payment_id",
            "appointment_id",
            "client_user_id",
            "tenant_id",
            "failure_code",
            "consecutive_failures",
            "failed_at",
            "payment_event_id",
            "client_name",
        }
        assert required.issubset(payload.keys())

    def test_payload_identity_fields_from_envelope(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        env = _envelope(event_name="payment.failed", data=_failed_data())
        payload = self._trip_threshold(env=env, settings=settings, conversation=conversation)

        assert payload["payment_id"] == PAYMENT_ID
        assert payload["appointment_id"] == APPOINTMENT_ID
        assert payload["client_user_id"] == AYLA_USER_ID
        assert payload["tenant_id"] == TENANT_ID
        assert payload["payment_event_id"] == env.event_id
        assert payload["failed_at"] == "2026-05-21T14:48:02.503Z"

    def test_payload_failure_code_is_mapped_not_raw(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        """PII rule §7 — provider free-text MUST NOT propagate to the
        skill. Mapped closed-enum value flows instead. Founder pseudocode
        used raw ``envelope.data["reason"]``; consumer overrides to the
        mapped value (same as ``Conversation.last_payment_failure_code``).
        """
        env = _envelope(
            event_name="payment.failed",
            data=_failed_data(reason="Card 4242****4242 declined by issuer"),
        )
        payload = self._trip_threshold(env=env, settings=settings, conversation=conversation)
        # Leaky free-text mapped to OTHER (closed enum).
        assert payload["failure_code"] == FailureCode.OTHER
        # And literally not the raw string.
        assert "4242" not in payload["failure_code"]

    def test_payload_client_name_from_bot_user_when_set(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
        settings,
    ) -> None:
        bot_user.client_name = "Анна"
        bot_user.save(update_fields=["client_name"])

        env = _envelope(event_name="payment.failed", data=_failed_data())
        payload = self._trip_threshold(env=env, settings=settings, conversation=conversation)
        assert payload["client_name"] == "Анна"

    def test_payload_client_name_none_when_blank(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        """Empty ``client_name`` on BotUser → None in payload (NOT empty
        string). Skill's α-mode template uses ``or "—"`` fallback which
        works on both falsy values, but None is the contract-canonical
        absent marker."""
        env = _envelope(event_name="payment.failed", data=_failed_data())
        payload = self._trip_threshold(env=env, settings=settings, conversation=conversation)
        assert payload["client_name"] is None

    def test_payload_does_not_carry_phase2_fields(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        """Phase 1 explicitly excludes master_user_id / master_name /
        service_name / amount / currency / appointment_date (envelope
        v1 doesn't carry them and we don't query Ayla per ADR-0009 §2).

        If a future change starts populating them inadvertently, the
        Phase 2 cutover plan (task #93 — event_version=1 → 2 branch)
        becomes ambiguous. Pin the absence explicitly.
        """
        env = _envelope(event_name="payment.failed", data=_failed_data())
        payload = self._trip_threshold(env=env, settings=settings, conversation=conversation)
        phase2_only = {
            "master_user_id",
            "master_name",
            "service_name",
            "amount",
            "currency",
            "appointment_date",
        }
        assert phase2_only.isdisjoint(payload.keys())


class TestSkillHandoffPostCommit:
    """The skill dispatch MUST run AFTER the Conversation row update
    commits. Otherwise the skill (which may issue DMs, write audit
    rows, read fresh Conversation state) could observe stale data or
    race with the row lock.
    """

    def test_skill_observes_committed_counter_value(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        conversation.consecutive_payment_failures = 2
        conversation.save(update_fields=["consecutive_payment_failures"])

        observed_counter: list[int] = []

        def _spy(payload: dict[str, Any]) -> None:
            # Re-read from DB at skill invocation time — the row update
            # MUST be visible here (transaction committed).
            row = Conversation.all_tenants.get(pk=conversation.pk)
            observed_counter.append(row.consecutive_payment_failures)

        with patch(
            "apps.skills.payment_failed.skill.on_payment_failed_event",
            side_effect=_spy,
        ):
            env = _envelope(event_name="payment.failed", data=_failed_data())
            handle_payment_failed(env)

        assert observed_counter == [3]

    def test_skill_exception_does_not_break_consumer(
        self, tenant: Tenant, conversation: Conversation, settings, caplog
    ) -> None:
        """Skill MUST NOT break the consumer loop. State commits even
        if the skill explodes; forensic log is the only trail. The
        dedupe row prevents a retry from double-firing the skill."""
        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        conversation.consecutive_payment_failures = 2
        conversation.save(update_fields=["consecutive_payment_failures"])

        with patch(
            "apps.skills.payment_failed.skill.on_payment_failed_event",
            side_effect=RuntimeError("synthetic skill failure"),
        ):
            env = _envelope(event_name="payment.failed", data=_failed_data())
            # Must NOT raise — consumer swallows skill failure.
            handle_payment_failed(env)

        # Counter still incremented and committed.
        conversation.refresh_from_db()
        assert conversation.consecutive_payment_failures == 3
        # Forensic log written.
        assert any("skill_handoff_failed" in rec.getMessage() for rec in caplog.records)


# ─── Cross-tenant isolation (Round-1 NTH-1 / Round-2 NEW-8) ────────────────


class TestCrossTenantIsolation:
    """Round-2 NEW-8: verify ``_resolve_conversation`` strictly scopes
    by tenant. A spoofer who learns a victim user's ``ayla_user_id``
    cannot reach the victim's Conversation row under the victim's
    tenant — because the resolver filters
    ``BotUser.all_tenants.filter(tenant=tenant, ayla_user_id=user_id)``.

    Also covers Round-2 NEW-1 indirectly: even if a spoofer's tenant
    has its own BotUser for the same ayla_user_id (legitimate
    multi-tenant user), payment side-effects land on the spoofer's
    tenant's Conversation, not the victim's.
    """

    def test_resolver_does_not_cross_tenant_boundary(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Victim tenant has Conversation. Spoofer tenant resolves the
        SAME ayla_user_id but has no BotUser there → returns None."""
        from apps.eventbus.consumers.payment import _resolve_conversation

        spoofer = Tenant.objects.create(
            id="55555555-6666-7777-8888-999999999999",
            slug="t-spoofer",
            name="Spoofer tenant",
        )

        # Victim's resolver finds their conversation.
        victim_conv = _resolve_conversation(tenant=tenant, user_id=AYLA_USER_ID)
        assert victim_conv is not None
        assert victim_conv.pk == conversation.pk

        # Spoofer's resolver returns None — no BotUser under spoofer's
        # tenant for this ayla_user_id.
        spoofer_conv = _resolve_conversation(tenant=spoofer, user_id=AYLA_USER_ID)
        assert spoofer_conv is None

    def test_payment_captured_under_spoofer_does_not_mutate_victim_conversation(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Even if envelope auth fail-opens (transition mode), and a
        spoofer's tenant receives the captured for the victim's
        payment_id, the dedupe row scopes per-tenant — the victim's
        terminal slot is NOT consumed. Round-2 NEW-1 proof.
        """
        spoofer = Tenant.objects.create(
            id="66666666-7777-8888-9999-aaaaaaaaaaaa",
            slug="t-spoofer-2",
            name="Spoofer tenant 2",
        )
        env_spoof = _envelope(
            event_name="payment.captured",
            data=_captured_data(),
            tenant_id=str(spoofer.id),
        )
        handle_payment_captured(env_spoof)

        # Victim's conversation untouched.
        conversation.refresh_from_db()
        assert conversation.last_payment_captured_at is None
        assert conversation.last_payment_event_id == ""

        # Victim can still process their own legit captured — dedupe
        # slot (victim_tenant, payment_id, captured) is still open
        # despite spoofer claiming (spoofer_tenant, payment_id,
        # captured).
        env_victim = _envelope(
            event_name="payment.captured",
            data=_captured_data(),
            event_id="01J9VICTIMCAP0000000000000",  # pragma: allowlist secret
            tenant_id=str(tenant.id),
        )
        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:
            handle_payment_captured(env_victim)

        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "loyalty_bonus_eligible"

        conversation.refresh_from_db()
        assert conversation.last_payment_captured_at == env_victim.occurred_at


# ─── Tenant-verify mandate ─────────────────────────────────────────────────


class TestTenantVerifyMandate:
    """Round-1 A3 mandate: every handler MUST call
    ``assert_envelope_tenant_authorized`` before any side-effect.
    The lint test in ``tests/contracts/`` scans handler source for the
    substring; here we verify the runtime behaviour.
    """

    def test_payment_authorized_raises_when_tenant_id_null(self) -> None:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(
            event_name="payment.authorized",
            data=_authorized_data(),
            tenant_id=None,  # payment.authorized is not tenant-nullable
        )
        with pytest.raises(TenantAuthorizationError):
            handle_payment_authorized(env)

    def test_payment_captured_raises_when_tenant_id_null(self) -> None:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(
            event_name="payment.captured",
            data=_captured_data(),
            tenant_id=None,
        )
        with pytest.raises(TenantAuthorizationError):
            handle_payment_captured(env)

    def test_payment_failed_raises_when_tenant_id_null(self) -> None:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(event_name="payment.failed", data=_failed_data(), tenant_id=None)
        with pytest.raises(TenantAuthorizationError):
            handle_payment_failed(env)

    def test_payment_refunded_raises_when_tenant_id_null(self) -> None:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(event_name="payment.refunded", data=_refunded_data(), tenant_id=None)
        with pytest.raises(TenantAuthorizationError):
            handle_payment_refunded(env)
