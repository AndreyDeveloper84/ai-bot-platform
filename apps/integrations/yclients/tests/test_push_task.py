"""Tests for the async YClients push task."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.integrations.yclients.client import BookingRecord
from apps.integrations.yclients.tasks import push_booking_to_yclients
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="yc-test",
        name="YC Test",
        timezone="Europe/Moscow",
        features={"yclients_integration": True},
    )


@pytest.fixture
def tenant_no_yc(db) -> Tenant:
    return Tenant.objects.create(
        slug="no-yc",
        name="No YC",
        timezone="Europe/Moscow",
        features={},
    )


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="1",
        chat_id="1",
        phone="+79991234567",
        client_name="Мария",
    )


def _make_booking(tenant, bot_user, *, yc_staff_id=42, yc_service_id=7) -> BookingRequest:
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Анна",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        yclients_staff_id=yc_staff_id,
    )
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
        raw={"yclients_service_id": yc_service_id} if yc_service_id else {},
    )
    with tenant_scope(tenant):
        return BookingRequest.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            service=service,
            master=master,
            service_name=service.name,
            master_name=master.name,
            client_name="Мария",
            client_phone="+79991234567",
            visit_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
            duration_min=60,
            booking_source="ai_direct",
            attribution_metadata={"actor_type": "customer", "created_by": "execute_confirm"},
        )


class TestPushBookingToYclients:
    def test_happy_path(self, tenant, bot_user) -> None:
        booking = _make_booking(tenant, bot_user)
        fake_record = BookingRecord(record_id=999, record_hash="h", raw={"id": 999})
        with patch("apps.integrations.yclients.client.get_yclients_client") as mock_client:
            mock_client.return_value.create_record.return_value = fake_record
            result = push_booking_to_yclients(booking_id=str(booking.id))
        assert result["status"] == "pushed"
        assert result["record_id"] == "999"
        booking.refresh_from_db()
        assert booking.attribution_metadata["yclients_record_id"] == "999"
        assert booking.attribution_metadata["yc_push_status"] == "pushed"

    def test_already_pushed_idempotent(self, tenant, bot_user) -> None:
        booking = _make_booking(tenant, bot_user)
        booking.attribution_metadata = {
            **booking.attribution_metadata,
            "yclients_record_id": "555",
        }
        booking.save()
        with patch("apps.integrations.yclients.client.get_yclients_client") as m:
            result = push_booking_to_yclients(booking_id=str(booking.id))
            m.assert_not_called()
        assert result["status"] == "already_pushed"

    def test_skipped_when_tenant_lacks_integration(self, tenant_no_yc) -> None:
        bot = BotUser.all_tenants.create(
            tenant=tenant_no_yc,
            channel="max",
            channel_user_id="2",
            chat_id="2",
            client_name="Б",
        )
        booking = _make_booking(tenant_no_yc, bot)
        with patch("apps.integrations.yclients.client.get_yclients_client") as m:
            result = push_booking_to_yclients(booking_id=str(booking.id))
            m.assert_not_called()
        assert result["status"] == "skipped_no_integration"

    def test_skipped_when_master_lacks_yc_staff_id(self, tenant, bot_user) -> None:
        booking = _make_booking(tenant, bot_user, yc_staff_id=None)
        with patch("apps.integrations.yclients.client.get_yclients_client") as m:
            result = push_booking_to_yclients(booking_id=str(booking.id))
            m.assert_not_called()
        assert result["status"] == "skipped_no_staff"

    def test_skipped_when_service_lacks_yc_mapping(self, tenant, bot_user) -> None:
        booking = _make_booking(tenant, bot_user, yc_service_id=None)
        with patch("apps.integrations.yclients.client.get_yclients_client") as m:
            result = push_booking_to_yclients(booking_id=str(booking.id))
            m.assert_not_called()
        assert result["status"] == "skipped_no_service_mapping"
