"""``cancel_booking`` + ``execute_cancel`` tool tests (B5 / DRF-841).

Preview path is non-destructive — only persists a
:class:`PendingBookingAction`. The actual YClients cancel + status
flip happens in :func:`execute_cancel` invoked by the gate-callback
on a ✅ tap. Both layers are covered here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingReminder, BookingRequest, PendingBookingAction
from apps.identity.models import BotUser
from apps.integrations.yclients import (
    Service,
    Staff,
    YClientsAPIError,
    YClientsUnavailableError,
)
from apps.skills.booking.tools import cancel_booking, execute_cancel
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cancel-tools", name="Cancel Tools")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-c",
        chat_id="bu-c",
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


class FakeClient:
    def __init__(self) -> None:
        self.cancel_calls: list[int] = []
        self.cancel_exc: Exception | None = None

    def cancel_record(self, *, record_id: int) -> bool:
        self.cancel_calls.append(record_id)
        if self.cancel_exc is not None:
            raise self.cancel_exc
        return True

    def get_staff(self, **_: Any) -> list[Staff]:
        return []

    def get_services(self, **_: Any) -> list[Service]:
        return []


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


# ---------------------------------------------------------------------------
# cancel_booking — preview
# ---------------------------------------------------------------------------


class TestCancelPreview:
    def test_happy_path_persists_pending(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        with tenant_scope(tenant):
            result = cancel_booking(
                client=client,
                arguments={"record_id": 555, "reason": "ребёнок заболел"},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        assert result.pending is not None
        assert result.pending.kind == PendingBookingAction.Kind.CANCEL
        assert len(result.pending.keyboard) == 2
        # No YClients call at preview time.
        assert client.cancel_calls == []
        # Pending row persisted with reason in payload.
        row = PendingBookingAction.all_tenants.get(pk=result.pending.token)
        assert row.payload["record_id"] == 555
        assert row.payload["reason"] == "ребёнок заболел"
        # BookingRequest still CONFIRMED — no flip at preview.
        booking = BookingRequest.all_tenants.get(comment__contains="yclients_record_id=555")
        assert booking.status == BookingRequest.Status.CONFIRMED

    def test_anti_hallucination_unknown_record(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        # No BookingRequest exists for this id.
        with tenant_scope(tenant):
            result = cancel_booking(
                client=client,
                arguments={"record_id": 9999},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_record_id"
        assert result.pending is None

    def test_cross_user_record_rejected(
        self, tenant: Tenant, bot_user: BotUser, other_user: BotUser
    ) -> None:
        # Booking belongs to other_user.
        _make_booking(tenant, other_user, yc_id=777)
        client = FakeClient()
        with tenant_scope(tenant):
            result = cancel_booking(
                client=client,
                arguments={"record_id": 777},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_record_id"

    def test_already_cancelled_booking_rejected(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_booking(
            tenant,
            bot_user,
            yc_id=888,
            status=BookingRequest.Status.CANCELLED,
        )
        client = FakeClient()
        with tenant_scope(tenant):
            result = cancel_booking(
                client=client,
                arguments={"record_id": 888},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_record_id"

    def test_missing_record_id(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        with tenant_scope(tenant):
            result = cancel_booking(
                client=client,
                arguments={},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_record_id"


# ---------------------------------------------------------------------------
# execute_cancel — the actual destructive call
# ---------------------------------------------------------------------------


class TestExecuteCancel:
    def test_happy_path_flips_status_and_cancels_reminders(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        # Add a pending reminder linked to this booking.
        visit_at = dj_timezone.now() + timedelta(days=1)
        BookingReminder.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="555",
            chat_id="bu-c",
            visit_at=visit_at,
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.PENDING,
            scheduled_at=visit_at - timedelta(hours=24),
            master_name="Ольга",
            service_name="Массаж",
        )
        client = FakeClient()
        with tenant_scope(tenant):
            result = execute_cancel(
                client=client,
                payload={"record_id": 555, "reason": "ребёнок заболел"},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        assert client.cancel_calls == [555]

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CANCELLED
        assert "cancelled: ребёнок заболел" in booking.comment
        # Original yc_id marker preserved.
        assert "yclients_record_id=555" in booking.comment
        # Reminder status flipped.
        reminder = BookingReminder.all_tenants.filter(
            yclients_record_id="555",
        ).first()
        assert reminder is not None
        assert reminder.status == BookingReminder.Status.CANCELLED

    def test_yclients_unavailable_does_not_flip_status(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.cancel_exc = YClientsUnavailableError("circuit_open")
        with tenant_scope(tenant):
            result = execute_cancel(
                client=client,
                payload={"record_id": 555},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "yclients_cancel_failure"
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED

    def test_yclients_api_error_does_not_flip_status(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.cancel_exc = YClientsAPIError("http_404")
        with tenant_scope(tenant):
            result = execute_cancel(
                client=client,
                payload={"record_id": 555},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "yclients_cancel_failure"
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED

    def test_invalid_payload_short_circuits(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        with tenant_scope(tenant):
            result = execute_cancel(
                client=client,
                payload={},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_payload"
        assert client.cancel_calls == []

    def test_reason_appended_not_overwritten(self, tenant: Tenant, bot_user: BotUser) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        original = booking.comment
        client = FakeClient()
        with tenant_scope(tenant):
            execute_cancel(
                client=client,
                payload={"record_id": 555, "reason": "просто"},
                tenant=tenant,
                bot_user=bot_user,
            )
        booking.refresh_from_db()
        assert booking.comment.startswith(original)
        assert "cancelled: просто" in booking.comment
