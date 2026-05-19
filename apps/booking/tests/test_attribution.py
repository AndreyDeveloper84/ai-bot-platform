"""Tests for the 4a attribution layer on BookingRequest."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.booking.models import BookingRequest
from apps.booking.services.attribution import (
    build_customer_attribution_metadata,
    compute_assist_score,
    compute_billable,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="att-test", name="Attribution Test")


class TestComputeBillable:
    def test_ai_direct_confirmed_billable(self) -> None:
        billable, reason = compute_billable(booking_source="ai_direct", status="confirmed")
        assert billable is True
        assert "ai_direct" in reason

    def test_ai_direct_cancelled_not_billable(self) -> None:
        billable, reason = compute_billable(booking_source="ai_direct", status="cancelled")
        assert billable is False
        assert "status=cancelled" in reason

    def test_human_direct_not_billable(self) -> None:
        billable, reason = compute_billable(booking_source="human_direct", status="confirmed")
        assert billable is False

    def test_test_admin_not_billable(self) -> None:
        billable, _ = compute_billable(booking_source="test_admin", status="confirmed")
        assert billable is False

    def test_external_not_billable(self) -> None:
        billable, _ = compute_billable(booking_source="external", status="confirmed")
        assert billable is False


class TestComputeAssistScore:
    def test_ai_direct_one(self) -> None:
        assert compute_assist_score(booking_source="ai_direct") == Decimal("1.00")

    def test_human_zero(self) -> None:
        assert compute_assist_score(booking_source="human_direct") == Decimal("0.00")


class TestBuildCustomerAttributionMetadata:
    def test_minimum_fields(self) -> None:
        meta = build_customer_attribution_metadata()
        assert meta["actor_type"] == "customer"
        assert meta["started_by"] == "customer"
        assert meta["created_by"] == "execute_confirm"
        assert meta["test_mode"] is False

    def test_optional_keys(self) -> None:
        meta = build_customer_attribution_metadata(
            conversation_id="abc",
            test_mode=True,
            booking_created_at="2026-05-18T10:00:00Z",
        )
        assert meta["conversation_id"] == "abc"
        assert meta["test_mode"] is True
        assert meta["booking_created_at"] == "2026-05-18T10:00:00Z"


class TestBookingRequestValidator:
    def test_legacy_external_no_validator(self, tenant: Tenant) -> None:
        # Legacy callsite — booking_source defaults to 'external', no actor_type.
        # Validator must NOT fire (transition concession).
        with tenant_scope(tenant):
            BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
            )

    def test_ai_direct_requires_actor_type(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            with pytest.raises(ValidationError, match="actor_type"):
                BookingRequest.objects.create(
                    tenant=tenant,
                    service_name="Manicure",
                    client_name="Test",
                    client_phone="+79991234567",
                    booking_source="ai_direct",
                )

    def test_ai_direct_invalid_actor_type_rejected(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            with pytest.raises(ValidationError, match="actor_type"):
                BookingRequest.objects.create(
                    tenant=tenant,
                    service_name="Manicure",
                    client_name="Test",
                    client_phone="+79991234567",
                    booking_source="ai_direct",
                    attribution_metadata={"actor_type": "intruder"},
                )

    def test_ai_direct_valid_actor_type_passes(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            req = BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=timezone.now() + timedelta(days=1),
                duration_min=60,
                booking_source="ai_direct",
                billable=True,
                billing_reason="ai_direct + confirmed",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
            )
        assert req.booking_source == "ai_direct"
        assert req.billable is True
        assert req.visit_at is not None
