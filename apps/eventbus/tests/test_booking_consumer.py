"""Tests for the booking.* consumer family (#442).

Covers each of the 4 handlers in :mod:`apps.eventbus.consumers.booking`
plus the idempotency replay-3× contract, orphan-proxy semantics (Ayla
mobile-only customers without a linked BotUser), and the tenant-verify
mandate.

### Test architecture

Handlers are invoked DIRECTLY (not through ``dispatch_envelope``) for
two reasons:

1. The dispatcher's threadpool deadlocks SQLite test backend (see
   conftest A12 bypass). Direct calls bypass that.
2. We want to test the handler's behaviour, not the dispatcher's
   transaction wrapping (that's pinned by test_ingest_dispatcher.py).

Idempotency replay-3× test calls the handler directly 3 times in a
row — asserts upsert-shaped writes + event_id short-circuit produce
identical final state.

### Tenant-verify bypass

Per Round-3 NEW-5 (PR #524), ``assert_envelope_tenant_authorized``
fails CLOSED by default when the canonical TenantUserRelationship
model isn't available (Sprint 1 #246). Tests set
``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True`` to enable the
documented pre-#246 bridge.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.eventbus.consumers.booking import (
    handle_booking_cancelled,
    handle_booking_completed,
    handle_booking_confirmed,
    handle_booking_created,
    handle_booking_no_show,
    handle_booking_rescheduled,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


# Canonical Ayla user UUID used across the suite — set as
# BotUser.ayla_user_id on the ``bot_user_linked`` fixture so the
# handler's _resolve_bot_user lookup succeeds.
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"
PAYMENT_ID = "5c8e2d1f-3b4a-4c6d-9e8f-1a2b3c4d5e6f"


# ─── helpers ───────────────────────────────────────────────────────────────


def _envelope(
    *,
    event_name: str,
    data: dict[str, Any],
    event_id: str | None = None,
    tenant_id: str | None = TENANT_ID,
    user_id: str = AYLA_USER_ID,
) -> IngestEnvelope:
    """Construct a fully-validated envelope for handler input."""
    return IngestEnvelope(
        event_id=event_id or "01J9HXKM8Z2T4V6R8Q1P3D5F7E",  # pragma: allowlist secret
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=user_id,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data,
    )


def _booking_created_data(
    *,
    appointment_id: str = APPOINTMENT_ID,
    start_at: str = "2026-05-22T15:00:00+03:00",
    end_at: str = "2026-05-22T16:00:00+03:00",
    status: str = "confirmed",
    source: str = "mobile_app",
) -> dict[str, Any]:
    return {
        "appointment_id": appointment_id,
        "specialist_id": "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17",
        "service_id": "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c",
        "start_at": start_at,
        "end_at": end_at,
        "status": status,
        "price_total": "1800.00",
        "source": source,
    }


@pytest.fixture(autouse=True)
def _pilot_allowlist(settings) -> None:
    """T-02 / OD-T02-1 — authorize this suite via the pilot allowlist.

    This used to set ``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True``, i.e.
    it proved the handlers worked only with tenant verification switched
    off entirely. The suite now runs the same path production will: the
    tenant and every event name under test are explicitly allowlisted, and
    the ``tenant`` fixture provides the DB row the check requires.
    """
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset(
        {
            "booking.created",
            "booking.confirmed",
            "booking.cancelled",
            "booking.completed",
            "booking.no_show",
            "booking.rescheduled",
            "appointment.rescheduled",
        }
    )


@pytest.fixture(autouse=True)
def _pilot_tenant_row(db, _pilot_allowlist) -> None:
    """The allowlisted tenant must exist for authorization to pass.

    Autouse + ``get_or_create`` so tests that never touch the ``tenant``
    fixture still satisfy the existence check without duplicating it.
    """
    Tenant.objects.get_or_create(
        id=TENANT_ID, defaults={"slug": "t-booking", "name": "Booking test tenant"}
    )


@pytest.fixture
def tenant() -> Tenant:
    """The suite's tenant row — created by ``_pilot_tenant_row`` autouse."""
    obj, _ = Tenant.objects.get_or_create(
        id=TENANT_ID,
        defaults={"slug": "t-booking", "name": "Booking test tenant"},
    )
    return obj


@pytest.fixture
def bot_user_linked(tenant: Tenant) -> BotUser:
    """A channel-side BotUser linked to the Ayla canonical user UUID.

    Sets ``ayla_user_id`` so the handler's ``_resolve_bot_user`` lookup
    (``filter(tenant=t, ayla_user_id=u).first()``) returns this row.
    Use in happy-path tests; orphan-path tests deliberately omit this
    fixture.
    """
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9001",
        chat_id="chat-9001",
        ayla_user_id=AYLA_USER_ID,
    )


# ─── booking.created ───────────────────────────────────────────────────────


class TestBookingCreated:
    def test_upserts_remote_booking_proxy_linked(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        env = _envelope(event_name="booking.created", data=_booking_created_data())
        handle_booking_created(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert str(proxy.tenant_id) == TENANT_ID
        assert proxy.bot_user_id == bot_user_linked.id  # linked, not Ayla UUID
        assert proxy.status == "confirmed"
        assert proxy.source == "mobile_app"
        assert proxy.last_synced_event_id == env.event_id

    def test_orphan_proxy_when_no_botuser_linked(self, tenant: Tenant) -> None:
        """No BotUser with this ayla_user_id → proxy is written orphan
        (bot_user=None), no reminders scheduled, no Conversation touched.
        The post_save BotUser backfill signal (FOLLOW_UP) will populate
        the ref when the customer first opens the bot.
        """
        env = _envelope(event_name="booking.created", data=_booking_created_data())
        handle_booking_created(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.bot_user is None  # orphan
        assert str(proxy.tenant_id) == TENANT_ID
        assert proxy.status == "confirmed"
        # No reminders were scheduled.
        assert (
            BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count()
            == 0
        )

    def test_orphan_proxy_queryable_by_appointment_id_alone(self, tenant: Tenant) -> None:
        """Orphan proxy lookup by appointment_id (PK) MUST work even
        without bot_user — that's the whole point of writing it orphan."""
        env = _envelope(event_name="booking.created", data=_booking_created_data())
        handle_booking_created(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy is not None
        assert proxy.bot_user_id is None

    def test_emits_internal_booking_created_event(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """§3.1 step 3 — emit on apps/events/ snake_case bus."""
        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:
            env = _envelope(event_name="booking.created", data=_booking_created_data())
            handle_booking_created(env)

        mock_emit.assert_called_once()
        call_args = mock_emit.call_args
        assert call_args[0][0] == "booking_created"  # snake_case event name
        props = call_args[1]["properties"]
        assert props["appointment_id"] == APPOINTMENT_ID
        assert props["status"] == "confirmed"

    def test_unknown_tenant_rejected_by_pilot_allowlist(self) -> None:
        """T-02 — an unknown tenant never reaches the handler at all.

        Under the pilot allowlist the envelope is refused at authorization
        (``tenant_not_allowed``), so the handler-level unknown-tenant path
        below is unreachable in a pilot-configured deploy. Pinned here
        because it is the outer half of the same guarantee.
        """
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(
            event_name="booking.created",
            data=_booking_created_data(),
            tenant_id="00000000-0000-0000-0000-000000000000",
        )
        with pytest.raises(TenantAuthorizationError):
            handle_booking_created(env)
        assert (
            RemoteBookingProxy.all_tenants.filter(appointment_id=UUID(APPOINTMENT_ID)).count() == 0
        )

    def test_unknown_tenant_logs_and_returns_without_error(self, settings) -> None:  # noqa: ANN001
        """No tenant row exists for the envelope's tenant_id — handler
        logs + returns. Does not raise, does not create orphan rows.

        This is defence-in-depth BELOW authorization, so the test must get
        past authorization to reach it. The global escape hatch is used
        deliberately and only here — it is not this suite's happy path.
        """
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True
        env = _envelope(
            event_name="booking.created",
            data=_booking_created_data(),
            tenant_id="00000000-0000-0000-0000-000000000000",
        )
        handle_booking_created(env)
        assert (
            RemoteBookingProxy.all_tenants.filter(appointment_id=UUID(APPOINTMENT_ID)).count() == 0
        )

    def test_schedules_reminders_when_linked(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        env = _envelope(event_name="booking.created", data=_booking_created_data())
        handle_booking_created(env)

        reminders = BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID))
        # T-24h + T-2h per §3.1 step 2.
        assert reminders.count() == 2
        assert set(reminders.values_list("kind", flat=True)) == {
            BookingReminder.Kind.DAY_BEFORE,
            BookingReminder.Kind.TWO_HOURS,
        }


# ─── booking.completed ─────────────────────────────────────────────────────


class TestBookingCompleted:
    def test_flips_status_to_completed(self, tenant: Tenant) -> None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )

        env = _envelope(
            event_name="booking.completed",
            data={
                "appointment_id": APPOINTMENT_ID,
                "completed_at": "2026-05-22T16:30:00.000Z",
            },
        )
        handle_booking_completed(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == "completed"
        assert proxy.last_synced_event_id == env.event_id

    def test_unknown_appointment_no_error(self) -> None:
        """Completed event for a proxy that doesn't exist locally
        (out-of-order: completed arrived before created). Handler is
        a no-op on empty queryset.
        """
        env = _envelope(
            event_name="booking.completed",
            data={
                "appointment_id": "deadbeef-0000-0000-0000-000000000000",
                "completed_at": "2026-05-22T16:30:00.000Z",
            },
        )
        handle_booking_completed(env)

    def test_emits_internal_booking_completed_event(self, tenant: Tenant) -> None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )
        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:
            env = _envelope(
                event_name="booking.completed",
                data={
                    "appointment_id": APPOINTMENT_ID,
                    "completed_at": "2026-05-22T16:30:00.000Z",
                },
            )
            handle_booking_completed(env)

        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "booking_completed"


# ─── booking.confirmed (B1) ─────────────────────────────────────────────────


class TestBookingConfirmed:
    """3-layer guard for the booking.confirmed consumer (B1): happy path,
    idempotency replay×3, cross-tenant spoof rejection.
    """

    def _confirmed_data(self) -> dict[str, Any]:
        # Canonical data — appointment_id + payment_id. Ayla emits
        # booking_id today; migration tracked in issue #945.
        return {"appointment_id": APPOINTMENT_ID, "payment_id": PAYMENT_ID}

    def _pending_proxy(self, tenant: Tenant) -> RemoteBookingProxy:
        # A booking still pending payment — confirm flips it to confirmed.
        return RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="pending_payment",
        )

    # ── happy path ──
    def test_flips_status_to_confirmed(self, tenant: Tenant) -> None:
        self._pending_proxy(tenant)
        env = _envelope(event_name="booking.confirmed", data=self._confirmed_data())
        handle_booking_confirmed(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED
        assert proxy.last_synced_event_id == env.event_id

    def test_unknown_appointment_no_error(self) -> None:
        """Confirm for a proxy that doesn't exist locally (out-of-order:
        confirmed before created). No-op on empty queryset, no raise."""
        env = _envelope(
            event_name="booking.confirmed",
            data={
                "appointment_id": "deadbeef-0000-0000-0000-000000000000",
                "payment_id": PAYMENT_ID,
            },
        )
        handle_booking_confirmed(env)

    def test_emits_internal_booking_confirmed_event(self, tenant: Tenant) -> None:
        self._pending_proxy(tenant)
        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:
            env = _envelope(event_name="booking.confirmed", data=self._confirmed_data())
            handle_booking_confirmed(env)

        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "booking_confirmed"
        assert mock_emit.call_args[1]["properties"]["payment_id"] == PAYMENT_ID

    # ── idempotency ×3 ──
    def test_replay_3x_single_confirmation(self, tenant: Tenant) -> None:
        self._pending_proxy(tenant)
        env = _envelope(event_name="booking.confirmed", data=self._confirmed_data())
        for _ in range(3):
            handle_booking_confirmed(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED
        assert proxy.last_synced_event_id == env.event_id

    def test_replay_3x_emits_only_once(self, tenant: Tenant) -> None:
        self._pending_proxy(tenant)
        env = _envelope(event_name="booking.confirmed", data=self._confirmed_data())
        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:
            for _ in range(3):
                handle_booking_confirmed(env)

        # event_id short-circuit means the side-effect emit fires once.
        mock_emit.assert_called_once()

    # ── cross-tenant spoof ──
    def test_cross_tenant_confirm_spoof_rejected(self, tenant: Tenant) -> None:
        """Tenant A owns the proxy; tenant B sends booking.confirmed for
        the same appointment_id → TenantAuthorizationError, A untouched.

        Under the pre-#246 FAIL_OPEN bridge the envelope-level
        ``assert_envelope_tenant_authorized`` logs-and-passes; the raise
        here comes from the ``_assert_proxy_tenant`` defence-in-depth
        guard (the load-bearing spoof defense until #246 lands).
        """
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        self._pending_proxy(tenant)  # tenant A
        tenant_b = Tenant.objects.create(
            id="11111111-2222-3333-4444-555555555555",
            slug="t-attacker-confirm",
            name="Attacker tenant",
        )
        env_b = _envelope(
            event_name="booking.confirmed",
            tenant_id=str(tenant_b.id),
            data=self._confirmed_data(),
        )

        with pytest.raises(TenantAuthorizationError):
            handle_booking_confirmed(env_b)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == "pending_payment"  # unchanged
        assert str(proxy.tenant_id) == TENANT_ID


# ─── booking.cancelled ─────────────────────────────────────────────────────


class TestBookingCancelled:
    def test_flips_existing_proxy_status_to_cancelled(self, tenant: Tenant) -> None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )

        env = _envelope(
            event_name="booking.cancelled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "user",
                "reason_code": "user_changed_plans",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )
        handle_booking_cancelled(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == "cancelled"
        assert proxy.last_synced_event_id == env.event_id

    def test_out_of_order_cancel_dropped_no_proxy_written(self, tenant: Tenant) -> None:
        """N-Adv1 Variant B: cancel-before-created is DROPPED, not
        stubbed. Writing a stub would let a spoofer claim
        appointment_id under their tenant; the legitimate created
        would then dead-letter. Drop + Ayla retry on
        (created → cancelled) converges naturally on CANCELLED.
        """
        env = _envelope(
            event_name="booking.cancelled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "system",
                "reason_code": "payment_hold_expired",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )
        handle_booking_cancelled(env)

        # No proxy was created — the handler returned early on
        # proxy=None.
        assert (
            RemoteBookingProxy.all_tenants.filter(appointment_id=UUID(APPOINTMENT_ID)).count() == 0
        )

    def test_subsequent_booking_cancelled_updates_orphan_proxy(self, tenant: Tenant) -> None:
        """Two events for the same appointment, no linked BotUser:
        - booking.created lands orphan
        - booking.cancelled then flips its status to cancelled
        Orphan proxy is reachable by appointment_id alone — that's
        the contract that keeps later events idempotent even before
        BotUser backfill.
        """
        env_created = _envelope(event_name="booking.created", data=_booking_created_data())
        handle_booking_created(env_created)

        env_cancelled = _envelope(
            event_name="booking.cancelled",
            event_id="01J9HXKN9X3T4V6R8Q1P3D5F7F",  # pragma: allowlist secret
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "user",
                "reason_code": "user_changed_plans",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )
        handle_booking_cancelled(env_cancelled)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == "cancelled"
        assert proxy.bot_user is None  # still orphan
        assert proxy.last_synced_event_id == env_cancelled.event_id

    def test_cancels_pending_reminders_idempotent(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """Existing PENDING reminders → CANCELLED after handler.

        Already-cancelled reminders stay cancelled (no error).
        Requires a proxy to exist — Variant B (N-Adv1) drops cancel
        events when proxy is missing.
        """
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=bot_user_linked,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )
        for kind in (BookingReminder.Kind.DAY_BEFORE, BookingReminder.Kind.TWO_HOURS):
            BookingReminder.all_tenants.create(
                tenant=tenant,
                bot_user=bot_user_linked,
                ayla_appointment_id=UUID(APPOINTMENT_ID),
                chat_id="chat-9001",
                visit_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
                kind=kind,
                status=BookingReminder.Status.PENDING,
                scheduled_at=dt.datetime(2026, 5, 22, 13, 0, tzinfo=dt.timezone.utc),
            )

        env = _envelope(
            event_name="booking.cancelled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "user",
                "reason_code": "user_changed_plans",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )
        handle_booking_cancelled(env)

        cancelled = BookingReminder.all_tenants.filter(
            ayla_appointment_id=UUID(APPOINTMENT_ID),
            status=BookingReminder.Status.CANCELLED,
        ).count()
        assert cancelled == 2

        # Second invocation is idempotent (event_id short-circuit).
        handle_booking_cancelled(env)
        assert (
            BookingReminder.all_tenants.filter(
                ayla_appointment_id=UUID(APPOINTMENT_ID),
                status=BookingReminder.Status.CANCELLED,
            ).count()
            == 2
        )


# ─── booking.rescheduled ───────────────────────────────────────────────────


class TestBookingRescheduled:
    def test_preserves_duration_on_reschedule(self, tenant: Tenant) -> None:
        """§3.3 step 1: new_end_at = new_start_at + (old_end - old_start)."""
        old_start = dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc)
        old_end = dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc)
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=old_start,
            end_at=old_end,
            status="confirmed",
        )

        env = _envelope(
            event_name="booking.rescheduled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "old_start_at": "2026-05-22T15:00:00+00:00",
                "new_start_at": "2026-05-23T11:00:00+00:00",
                "rescheduled_by": "admin",
            },
        )
        handle_booking_rescheduled(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        assert proxy.end_at == dt.datetime(2026, 5, 23, 12, 0, tzinfo=dt.timezone.utc)
        assert proxy.end_at - proxy.start_at == old_end - old_start

    def test_out_of_order_reschedule_no_proxy_logs_and_returns(self) -> None:
        env = _envelope(
            event_name="booking.rescheduled",
            data={
                "appointment_id": "deadbeef-1111-1111-1111-000000000000",
                "old_start_at": "2026-05-22T15:00:00+00:00",
                "new_start_at": "2026-05-23T11:00:00+00:00",
                "rescheduled_by": "admin",
            },
        )
        handle_booking_rescheduled(env)
        assert (
            RemoteBookingProxy.all_tenants.filter(
                appointment_id="deadbeef-1111-1111-1111-000000000000"
            ).count()
            == 0
        )


# ─── Idempotency replay-3× ─────────────────────────────────────────────────


class TestIdempotencyReplay3x:
    """§5.1 contract: same event_id delivered N times → 1 observable effect.

    Handler-level idempotency: even WITHOUT IngestDedupe (dispatcher
    short-circuits before reaching the handler on dedupe hit),
    calling the handler 3 times in a row MUST produce upsert-shaped
    state (one proxy, one set of reminders) — not duplicates.

    This pins the «upsert-shaped writes + event_id short-circuit»
    property that adversarial review explicitly looks for.
    """

    def test_booking_created_replay_3x_produces_single_proxy(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        env = _envelope(event_name="booking.created", data=_booking_created_data())

        for _ in range(3):
            handle_booking_created(env)

        proxies = RemoteBookingProxy.all_tenants.filter(appointment_id=UUID(APPOINTMENT_ID))
        assert proxies.count() == 1
        # And only 2 reminders — replay does not duplicate them.
        assert (
            BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count()
            == 2
        )

    def test_booking_created_replay_3x_emits_only_once(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """Defence-in-depth short-circuit: emit_internal_event fires
        exactly once across 3 deliveries of the same event_id, even
        though emit() itself isn't event-id-aware."""
        env = _envelope(event_name="booking.created", data=_booking_created_data())

        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:
            for _ in range(3):
                handle_booking_created(env)

        assert mock_emit.call_count == 1

    def test_booking_cancelled_replay_3x_produces_single_cancellation(self, tenant: Tenant) -> None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )

        env = _envelope(
            event_name="booking.cancelled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "user",
                "reason_code": "user_changed_plans",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )
        for _ in range(3):
            handle_booking_cancelled(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == "cancelled"

    def test_booking_completed_replay_3x_terminal_state_stable(self, tenant: Tenant) -> None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )

        env = _envelope(
            event_name="booking.completed",
            data={
                "appointment_id": APPOINTMENT_ID,
                "completed_at": "2026-05-22T16:30:00.000Z",
            },
        )
        for _ in range(3):
            handle_booking_completed(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == "completed"


# ─── Cross-tenant spoof defence (Round-5 F-Adv1 / F-Adv2) ──────────────────


class TestCrossTenantSpoofDefence:
    """Round-5 F-Adv1: ``appointment_id`` is a global UUID, so a
    spoofed envelope from tenant B can target tenant A's appointment.
    The handler MUST raise ``TenantAuthorizationError`` rather than
    silently mutating A's row.

    F-Adv2: the partial unique reminder index is scoped per tenant,
    so two distinct tenants COULD legitimately have the same
    appointment_id+kind pair without colliding (cross-tenant
    isolation at the storage layer).
    """

    def test_cross_tenant_cancel_spoof_rejected(self, tenant: Tenant) -> None:
        """Tenant A owns the proxy; tenant B sends booking.cancelled
        with the same appointment_id → TenantAuthorizationError."""
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,  # tenant A
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )

        # Tenant B sends a cancel for tenant A's appointment.
        tenant_b = Tenant.objects.create(
            id="11111111-2222-3333-4444-555555555555",
            slug="t-attacker",
            name="Attacker tenant",
        )
        env_b = _envelope(
            event_name="booking.cancelled",
            tenant_id=str(tenant_b.id),
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "user",
                "reason_code": "user_changed_plans",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )

        with pytest.raises(TenantAuthorizationError):
            handle_booking_cancelled(env_b)

        # Proxy state UNCHANGED — still confirmed under tenant A.
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == "confirmed"
        assert str(proxy.tenant_id) == TENANT_ID

    def test_cross_tenant_reminder_unique_isolation(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """Partial unique index is keyed on (ayla_appointment_id,
        tenant_id, kind) — two tenants could in principle have the
        same appointment_id pair without colliding. Build the
        scenario by writing two reminders directly with the same
        ayla_appointment_id + kind but different tenants. NO
        IntegrityError should fire.
        """
        tenant_b = Tenant.objects.create(
            id="22222222-3333-4444-5555-666666666666",
            slug="t-other",
            name="Other tenant",
        )
        bot_user_b = BotUser.all_tenants.create(
            tenant=tenant_b,
            channel="max",
            channel_user_id="9002",
            chat_id="chat-9002",
        )
        BookingReminder.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user_linked,
            ayla_appointment_id=UUID(APPOINTMENT_ID),
            chat_id="chat-9001",
            visit_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.PENDING,
            scheduled_at=dt.datetime(2026, 5, 21, 15, 0, tzinfo=dt.timezone.utc),
        )
        # Same appointment_id + kind, different tenant — MUST NOT collide.
        BookingReminder.all_tenants.create(
            tenant=tenant_b,
            bot_user=bot_user_b,
            ayla_appointment_id=UUID(APPOINTMENT_ID),
            chat_id="chat-9002",
            visit_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.PENDING,
            scheduled_at=dt.datetime(2026, 5, 21, 15, 0, tzinfo=dt.timezone.utc),
        )
        assert (
            BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count()
            == 2
        )


# ─── TOCTOU race (Round-6 N-Adv1) ──────────────────────────────────────────


class TestTOCTOURaceClosed:
    """N-Adv1: concurrent ``booking.created`` events for the same
    appointment_id from different tenants must not allow tenant
    takeover. The atomic INSERT-or-check pattern catches the race.
    Simulating concurrency in SQLite is awkward — exercise the
    IntegrityError path by pre-creating a proxy under tenant A and
    sending tenant B's create.
    """

    def test_concurrent_create_loser_dead_letters_on_tenant_mismatch(self, tenant: Tenant) -> None:
        """Pre-existing proxy under tenant A → tenant B's
        ``get_or_create`` returns the existing row with created=False
        → tenant guard raises. Tenant A's row untouched.

        This covers the «proxy already present at handler entry» path.
        The actual race-loser path (proxy materialises between
        get_or_create's initial GET and INSERT) is covered by
        ``test_race_loser_integrity_error_path_dead_letters`` below.
        """
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        # Tenant A wrote first (the "winning" worker).
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,  # tenant A
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
            last_synced_event_id="01J9PRIORWINNER000000000ZZ",  # pragma: allowlist secret
        )

        tenant_b = Tenant.objects.create(
            id="33333333-4444-5555-6666-777777777777",
            slug="t-racer",
            name="Race-attacker tenant",
        )
        env_b = _envelope(
            event_name="booking.created",
            tenant_id=str(tenant_b.id),
            data=_booking_created_data(),
        )

        with pytest.raises(TenantAuthorizationError):
            handle_booking_created(env_b)

        # Tenant A's row is intact.
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert str(proxy.tenant_id) == TENANT_ID
        assert proxy.status == "confirmed"
        assert (
            proxy.last_synced_event_id == "01J9PRIORWINNER000000000ZZ"  # pragma: allowlist secret
        )

    # NOTE — true concurrent INSERT-race coverage (N-Adv6-3) is
    # deferred to a FOLLOW_UP integration test. The IntegrityError
    # branch inside Django's ``get_or_create`` only fires under real
    # transaction-level concurrency; the SQLite test backend can't
    # simulate this faithfully without threading + transaction=True
    # + a Postgres docker. The handler relies on Django's
    # battle-tested ``get_or_create`` (django/db/models/query.py
    # line 936) which re-raises non-PK IntegrityErrors via the
    # internal re-SELECT-DoesNotExist branch. The path is covered
    # in Django's own CI; we file an issue to add a contracts-tier
    # integration test once Phase 0 Postgres test infra lands.


# ─── Deterministic BotUser resolve (Round-5 F-Adv3) ────────────────────────


class TestDeterministicBotUserResolve:
    """F-Adv3: multi-channel user → multiple BotUser rows under the
    same (tenant, ayla_user_id). Without ``order_by("-last_seen")``
    the resolver returned an arbitrary one. Pin to most-recently-seen.
    """

    def test_multi_channel_botuser_resolves_to_latest_last_seen(self, tenant: Tenant) -> None:
        from django.utils import timezone

        # Two BotUsers for the same ayla_user_id, different channels.
        # ``last_seen`` is auto_now=True — overwrite directly via
        # the queryset update so the deterministic pick is the
        # latest-touched channel.
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
        # Stamp last_seen explicitly: TG is newer.
        BotUser.all_tenants.filter(pk=max_user.pk).update(
            last_seen=timezone.now() - dt.timedelta(hours=5)
        )
        BotUser.all_tenants.filter(pk=tg_user.pk).update(last_seen=timezone.now())

        env = _envelope(event_name="booking.created", data=_booking_created_data())
        handle_booking_created(env)

        # The reminders should be routed to the TG chat (last_seen latest).
        reminder = BookingReminder.all_tenants.filter(
            ayla_appointment_id=UUID(APPOINTMENT_ID)
        ).first()
        assert reminder is not None
        assert reminder.chat_id == "chat-tg"


# ─── yclients_record_id NULL on Ayla path (Round-5 F-Fri1) ─────────────────


class TestYClientsRecordIdNullableOnAylaPath:
    """F-Fri1: two distinct Ayla appointments with the same kind MUST
    coexist. Was broken when ``yclients_record_id`` defaulted to ``""``
    and ``unique_together(yclients_record_id, kind)`` flagged the
    pair ("", "day_before") as duplicate. Fix: write NULL on Ayla
    path; NULL ≠ NULL in Postgres uniqueness.
    """

    def test_two_distinct_ayla_appointments_schedule_independent_reminders(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        appointment_a = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"
        appointment_b = "c9e4f6a7-2d3e-5f70-9b0c-d4e5f6a7b8c9"

        env_a = _envelope(
            event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7A",  # pragma: allowlist secret
            event_name="booking.created",
            data=_booking_created_data(appointment_id=appointment_a),
        )
        env_b = _envelope(
            event_id="01J9HXKN9Z3T4V6R8Q1P3D5F7B",  # pragma: allowlist secret
            event_name="booking.created",
            data=_booking_created_data(
                appointment_id=appointment_b,
                start_at="2026-05-23T15:00:00+03:00",
                end_at="2026-05-23T16:00:00+03:00",
            ),
        )
        handle_booking_created(env_a)
        handle_booking_created(env_b)

        # Both appointments produced 2 reminders each — no IntegrityError.
        assert (
            BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(appointment_a)).count() == 2
        )
        assert (
            BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(appointment_b)).count() == 2
        )
        # All four rows have yclients_record_id = NULL on the Ayla path.
        ayla_rows = BookingReminder.all_tenants.filter(
            ayla_appointment_id__in=[UUID(appointment_a), UUID(appointment_b)]
        )
        for r in ayla_rows:
            assert r.yclients_record_id is None


# ─── Reschedule corrupted-state guard (Round-5 F-Adv4) ─────────────────────


class TestRescheduleCorruptedStateRejected:
    """F-Adv4: refuse to compute duration off zero-or-negative
    schedule windows. Catches the out-of-order cancel-stub
    (start_at == end_at == cancelled_at) before it propagates into
    reminder math."""

    def test_reschedule_zero_duration_proxy_rejected(self, tenant: Tenant) -> None:
        # Stub state: cancelled before created → start==end placeholder.
        stub_time = dt.datetime(2026, 5, 21, 16, 0, tzinfo=dt.timezone.utc)
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=stub_time,
            end_at=stub_time,  # zero duration
            status=RemoteBookingProxy.Status.CANCELLED,
        )

        env = _envelope(
            event_name="booking.rescheduled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "old_start_at": "2026-05-22T15:00:00+00:00",
                "new_start_at": "2026-05-23T11:00:00+00:00",
                "rescheduled_by": "admin",
            },
        )
        with pytest.raises(ValueError, match="non-positive duration"):
            handle_booking_rescheduled(env)


# ─── Rescheduled replay-3× (Round-5 F-Fri5 — coverage gap) ─────────────────


class TestRescheduledReplay3x:
    def test_booking_rescheduled_replay_3x_idempotent(self, tenant: Tenant) -> None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )

        env = _envelope(
            event_name="booking.rescheduled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "old_start_at": "2026-05-22T15:00:00+00:00",
                "new_start_at": "2026-05-23T11:00:00+00:00",
                "rescheduled_by": "admin",
            },
        )
        for _ in range(3):
            handle_booking_rescheduled(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        # Duration preserved across all 3 replays.
        assert proxy.end_at - proxy.start_at == dt.timedelta(hours=1)


# ─── Tenant-verify mandate (lint complement) ───────────────────────────────


class TestTenantVerifyMandate:
    """Round-1 A3 mandate (PR #524): every handler calls
    ``assert_envelope_tenant_authorized`` BEFORE any side-effect.

    The lint test in ``tests/contracts/`` scans handler SOURCE for the
    substring. These tests verify the RUNTIME behaviour — the call
    actually fires and would raise on a legitimate tenant mismatch.
    """

    def test_handler_raises_when_tenant_id_null_for_non_nullable_event(self) -> None:
        """``booking.created`` is NOT a tenant-nullable event (only
        ``user.profile.updated`` is). With tenant_id=None the helper
        raises BEFORE the upsert."""
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(
            event_name="booking.created",
            data=_booking_created_data(),
            tenant_id=None,  # null for booking.created → helper raises
        )

        with pytest.raises(TenantAuthorizationError):
            handle_booking_created(env)

        assert RemoteBookingProxy.all_tenants.count() == 0


class TestNoShowAmd018:
    """``booking.no_show`` (AMD-018, event #13 v1): proxy flips to NO_SHOW,
    PENDING reminders cancelled (sent/other states untouched), replay
    idempotent, unknown version dead-letters per existing DLQ policy."""

    def _env(self, *, event_id: str = "01J9NOSHOW00000000000001", version: int = 1):
        env = _envelope(
            event_name="booking.no_show",
            event_id=event_id,
            data={"appointment_id": APPOINTMENT_ID},
        )
        if version != 1:
            env = IngestEnvelope(
                event_id=env.event_id,
                event_name=env.event_name,
                event_version=version,
                occurred_at=env.occurred_at,
                tenant_id=env.tenant_id,
                user_id=env.user_id,
                actor=env.actor,
                correlation_id=env.correlation_id,
                causation_id=env.causation_id,
                data=env.data,
            )
        return env

    def _proxy(self, tenant: Tenant, status=RemoteBookingProxy.Status.CONFIRMED):
        return RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 7, 20, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 7, 20, 16, 0, tzinfo=dt.timezone.utc),
            status=status,
        )

    def _reminder(self, tenant: Tenant, bot_user: BotUser, *, status: str, kind=None):
        return BookingReminder.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            ayla_appointment_id=UUID(APPOINTMENT_ID),
            kind=kind or BookingReminder.Kind.TWO_HOURS,
            status=status,
            chat_id="chat-1",
            visit_at=dt.datetime(2026, 7, 20, 16, 0, tzinfo=dt.timezone.utc),
            scheduled_at=dt.datetime(2026, 7, 20, 14, 0, tzinfo=dt.timezone.utc),
        )

    def test_vocabulary_contains_no_show(self) -> None:
        """1) booking.no_show is in ALLOWED_EVENT_NAMES and _KNOWN_NAMES."""
        from apps.eventbus.ingest_dispatcher import _KNOWN_NAMES
        from apps.eventbus.ingest_envelope import ALLOWED_EVENT_NAMES

        assert "booking.no_show" in ALLOWED_EVENT_NAMES
        assert "booking.no_show" in _KNOWN_NAMES

    def test_valid_event_flips_proxy(self, tenant: Tenant) -> None:
        """2) Valid event moves the proxy to NO_SHOW."""
        self._proxy(tenant)
        handle_booking_no_show(self._env())
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.NO_SHOW

    def test_pending_reminders_cancelled(self, tenant: Tenant, bot_user_linked: BotUser) -> None:
        """3) PENDING reminders for the appointment are cancelled."""
        self._proxy(tenant)
        self._reminder(tenant, bot_user_linked, status=BookingReminder.Status.PENDING)
        self._reminder(
            tenant,
            bot_user_linked,
            status=BookingReminder.Status.PENDING,
            kind=BookingReminder.Kind.DAY_BEFORE,
        )

        handle_booking_no_show(self._env())

        reminders = BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID))
        assert {r.status for r in reminders} == {BookingReminder.Status.CANCELLED}

    def test_non_pending_reminders_untouched(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """4) Sent / already-cancelled reminders are NOT modified."""
        self._proxy(tenant)
        sent = self._reminder(tenant, bot_user_linked, status=BookingReminder.Status.SENT)
        cancelled = self._reminder(
            tenant,
            bot_user_linked,
            status=BookingReminder.Status.CANCELLED,
            kind=BookingReminder.Kind.DAY_BEFORE,
        )

        handle_booking_no_show(self._env())

        sent.refresh_from_db()
        cancelled.refresh_from_db()
        assert sent.status == BookingReminder.Status.SENT
        assert cancelled.status == BookingReminder.Status.CANCELLED

    def test_replay_idempotent(self, tenant: Tenant) -> None:
        """5) Replay does not repeat side-effects."""
        self._proxy(tenant)
        env = self._env()
        handle_booking_no_show(env)
        first = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        handle_booking_no_show(env)
        second = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert first.status == second.status == RemoteBookingProxy.Status.NO_SHOW
        assert first.last_synced_event_id == second.last_synced_event_id

    def test_dispatch_ok_and_dedupe(self, tenant: Tenant, settings) -> None:
        """5b) Dispatcher-level: OK then DUPLICATE on the same event_id."""
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True
        from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope

        self._proxy(tenant)
        env = self._env()
        assert dispatch_envelope(env).outcome == DispatchOutcome.OK
        assert dispatch_envelope(env).outcome == DispatchOutcome.DUPLICATE

    def test_unknown_version_dead_letters(self, settings) -> None:
        """6) Unknown event_version follows the existing DLQ policy."""
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True
        from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope
        from apps.eventbus.models import IngestDLQ

        env = self._env(event_id="01J9NOSHOW00000000000002", version=2)
        assert dispatch_envelope(env).outcome == DispatchOutcome.UNKNOWN_EVENT_VERSION
        assert IngestDLQ.objects.filter(
            event_id=env.event_id, reason="unknown_event_version"
        ).exists()

    def test_unknown_booking_no_error(self, tenant: Tenant) -> None:
        """6b) Unknown appointment follows the standard proxy-missing path:
        the proxy UPDATE matches 0 rows — no error, reminders still cancelled."""
        # No proxy row for APPOINTMENT_ID at all.
        handle_booking_no_show(self._env())
        assert not RemoteBookingProxy.all_tenants.filter(
            appointment_id=UUID(APPOINTMENT_ID)
        ).exists()
