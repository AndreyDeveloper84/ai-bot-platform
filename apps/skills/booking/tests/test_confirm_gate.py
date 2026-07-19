"""2-button confirmation gate tests (B5 / DRF-841, absorbs B4 / DRF-840).

Covers the preview-tap-execute flow at the pending-action layer.
End-to-end skill-→-callback integration lives in
:mod:`apps.bookings.tests.test_booking_callbacks`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.booking.models import BookingRequest, PendingBookingAction
from apps.bookings.pending_actions import (
    PENDING_ACTION_TTL,
    consume_pending,
    create_pending,
    discard_pending,
)
from apps.identity.models import BotUser
from apps.integrations.yclients import BookingRecord
from apps.skills.booking.tools import execute_confirm
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="gate", name="Gate")


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


class FakeClient:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.create_response: BookingRecord | None = None

    def create_record(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.create_response or BookingRecord(record_id=777, record_hash="h", raw={})


def _make_pending(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    kind: str = PendingBookingAction.Kind.CONFIRM,
    payload: dict | None = None,
):
    return create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=kind,
        payload=payload
        or {
            "master_id": 11,
            "service_id": 22,
            "slot_datetime": "2026-05-20T14:00:00",
            "client_phone": "79991234567",
            "client_name": "Anna",
            "master_name": "Ольга",
            "service_name": "Массаж",
        },
    )


# ---------------------------------------------------------------------------
# create_pending / consume_pending lifecycle
# ---------------------------------------------------------------------------


class TestPendingLifecycle:
    def test_create_stores_row_with_ttl(self, tenant: Tenant, bot_user: BotUser) -> None:
        token = _make_pending(tenant, bot_user)
        row = PendingBookingAction.all_tenants.get(pk=token)
        # TTL == default; expires_at is roughly now + 10min.
        delta = row.expires_at - row.created_at
        assert abs(delta - PENDING_ACTION_TTL) < timedelta(seconds=5)
        assert row.consumed_at is None

    def test_consume_happy_path(self, tenant: Tenant, bot_user: BotUser) -> None:
        token = _make_pending(tenant, bot_user)
        lookup = consume_pending(token)
        assert lookup.ok is True
        assert lookup.row is not None
        assert lookup.row.consumed_at is not None

    def test_consume_expired_returns_expired(self, tenant: Tenant, bot_user: BotUser) -> None:
        token = _make_pending(tenant, bot_user)
        # Force expiry by editing the row.
        PendingBookingAction.all_tenants.filter(pk=token).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        lookup = consume_pending(token)
        assert lookup.expired is True
        assert lookup.ok is False

    def test_consume_already_consumed(self, tenant: Tenant, bot_user: BotUser) -> None:
        token = _make_pending(tenant, bot_user)
        consume_pending(token)
        lookup = consume_pending(token)
        assert lookup.already_consumed is True
        assert lookup.ok is False

    def test_consume_unknown_token_returns_none_row(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        import uuid

        lookup = consume_pending(uuid.uuid4())
        assert lookup.row is None
        assert lookup.ok is False

    def test_discard_marks_consumed(self, tenant: Tenant, bot_user: BotUser) -> None:
        token = _make_pending(tenant, bot_user)
        ok = discard_pending(token)
        assert ok is True
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.consumed_at is not None

    def test_discard_after_consume_is_noop(self, tenant: Tenant, bot_user: BotUser) -> None:
        token = _make_pending(tenant, bot_user)
        consume_pending(token)
        ok = discard_pending(token)
        assert ok is False


# ---------------------------------------------------------------------------
# execute_confirm — actual record creation on ✅
# ---------------------------------------------------------------------------


class TestExecuteConfirm:
    def test_creates_record_and_booking_request(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        client.create_response = BookingRecord(record_id=12345, record_hash="h", raw={})
        future_iso = (timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
        with tenant_scope(tenant):
            result = execute_confirm(
                client=client,
                payload={
                    "master_id": 11,
                    "service_id": 22,
                    "slot_datetime": future_iso,
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        assert result.confirmation is not None
        assert result.confirmation.ok is True
        assert client.create_calls == [
            {
                "staff_id": 11,
                "services": [22],
                "datetime": future_iso,
                "client_phone": "79991234567",
                "client_name": "Anna",
            }
        ]
        row = BookingRequest.all_tenants.get(
            tenant=tenant,
            bot_user=bot_user,
            comment__contains="yclients_record_id=12345",
        )
        assert row.status == BookingRequest.Status.CONFIRMED

    def test_idempotent_on_duplicate_record(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        client.create_response = BookingRecord(record_id=12345, record_hash="h", raw={})
        payload = {
            "master_id": 11,
            "service_id": 22,
            "slot_datetime": "2026-05-20T14:00:00",
            "client_phone": "79991234567",
            "client_name": "Anna",
            "master_name": "Ольга",
            "service_name": "Массаж",
        }
        with tenant_scope(tenant):
            execute_confirm(client=client, payload=payload, tenant=tenant, bot_user=bot_user)
            execute_confirm(client=client, payload=payload, tenant=tenant, bot_user=bot_user)
        assert BookingRequest.all_tenants.filter(tenant=tenant, bot_user=bot_user).count() == 1

    def test_c1_specialist_unavailable_neutral_slug(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """C1: Ayla 409 SUBSCRIPTION_PAST_DUE surfaces as the NEUTRAL
        specialist_unavailable slug — the debt reason never reaches the
        client surface (PILOT_CONTRACTS_2026-08-15 §2)."""
        from apps.skills.booking.provider import YClientsSpecialistUnavailableError

        exc = YClientsSpecialistUnavailableError("http_409_subscription_past_due")

        class _DebtBlockedClient(FakeClient):
            def create_record(self, **kwargs):  # type: ignore[no-untyped-def]
                raise exc

        client = _DebtBlockedClient()
        with tenant_scope(tenant):
            result = execute_confirm(
                client=client,
                payload={
                    "master_id": 11,
                    "service_id": 22,
                    "slot_datetime": "2026-05-20T14:00:00",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "specialist_unavailable"
        assert result.confirmation is not None
        assert result.confirmation.ok is False
        assert result.confirmation.error == "specialist_unavailable"
        # The slug carries no debt semantics.
        assert "past_due" not in result.error
        assert "subscription" not in result.error

    def test_invalid_payload(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        with tenant_scope(tenant):
            result = execute_confirm(
                client=client,
                payload={"master_id": 11},  # missing service_id + slot
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_payload"
        assert client.create_calls == []

    def test_writes_ai_direct_attribution(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Phase 4a-IMPL1 closure: bot-confirm path = ai_direct + billable.

        Without these fields BookingRequest.booking_source defaults to
        'external' and bot-created bookings would silently fall out of
        the billing pipeline.
        """
        from decimal import Decimal

        client = FakeClient()
        client.create_response = BookingRecord(record_id=42, record_hash="h", raw={})
        future_iso = (timezone.now() + timedelta(days=3)).replace(microsecond=0).isoformat()
        with tenant_scope(tenant):
            execute_confirm(
                client=client,
                payload={
                    "master_id": 11,
                    "service_id": 22,
                    "slot_datetime": future_iso,
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        row = BookingRequest.all_tenants.get(
            tenant=tenant,
            bot_user=bot_user,
            comment__contains="yclients_record_id=42",
        )
        assert row.booking_source == "ai_direct"
        assert row.billable is True
        assert row.billing_reason  # populated when billable
        assert row.ai_assist_score == Decimal("1.00")
        # attribution_metadata required-keys per attribution-policy §4.
        assert row.attribution_metadata["actor_type"] == "customer"
        assert row.attribution_metadata["created_by"] == "execute_confirm"
        assert row.attribution_metadata["started_by"] == "customer"
        assert row.attribution_metadata["test_mode"] is False
        # visit_at populated — Q-ATT-IMPL3 validator passes for non-external.
        assert row.visit_at is not None

    def test_unparseable_slot_rejects_before_yclients_call(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Phase 4a-IMPL1 split-brain guard: if slot_datetime is
        unparseable, reject BEFORE calling YClients so we don't leave a
        YClients record without a corresponding BookingRequest row.
        """
        client = FakeClient()
        client.create_response = BookingRecord(record_id=99, record_hash="h", raw={})
        with tenant_scope(tenant):
            result = execute_confirm(
                client=client,
                payload={
                    "master_id": 11,
                    "service_id": 22,
                    "slot_datetime": "garbage-not-iso",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_payload"
        # YClients never called — no split-brain.
        assert client.create_calls == []
        assert BookingRequest.all_tenants.filter(tenant=tenant).count() == 0
