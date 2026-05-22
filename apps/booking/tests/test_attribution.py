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

    # ---- Q12-α continuation chain (issue #478 — founder ACK 2026-05-22) ----

    def test_q12a_reschedule_without_continuation_kwarg_is_billable(self) -> None:
        """Default path: a fresh ai_direct + CONFIRMED + created_by=execute_confirm
        is billable. Reschedule call sites pass the explicit
        ``is_reschedule_continuation`` flag — when neither is set, the
        behaviour matches the pre-Q12-α default (billable=True)."""

        billable, _ = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_confirm",
        )
        assert billable is True

    def test_q12a_continuation_marks_not_billable(self) -> None:
        """Continuation chain (same service, ≤90d, no break): a reschedule's
        new row is NOT billable. ``billing_reason='reschedule_continuation'``
        so finance reports can dedupe these explicitly."""

        billable, reason = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_reschedule",
            is_reschedule_continuation=True,
        )
        assert billable is False
        assert reason == "reschedule_continuation"

    def test_q12a_chain_break_service_swap_is_billable(self) -> None:
        """Service swap breaks the chain → new row is billable. Founder
        decision 2026-05-22: «strict service_id equality. Category/price-
        equivalent logic не делаем»."""

        billable, reason = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_reschedule",
            is_reschedule_continuation=False,
            chain_break_reason="service_swap",
        )
        assert billable is True
        assert "service_swap" in reason

    def test_q12a_chain_break_over_90d_is_billable(self) -> None:
        """>90 days from chain root visit_at breaks the chain. Founder
        decision 2026-05-22: «90 days from original root visit_at»."""

        billable, reason = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_reschedule",
            is_reschedule_continuation=False,
            chain_break_reason="over_90d",
        )
        assert billable is True
        assert "over_90d" in reason

    def test_q12a_reschedule_without_continuation_flag_defaults_to_break(
        self,
    ) -> None:
        """Defence: if a future caller forgets to compute continuation
        (passes ``created_by=execute_reschedule`` without
        ``is_reschedule_continuation``), the safe default is to treat the
        chain as broken (= billable). Better to over-charge a customer
        than to silently undercharge the salon."""

        billable, reason = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_reschedule",
        )
        assert billable is True
        assert "chain_broken" in reason or "missing_continuation_signal" in reason


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


# ---------------------------------------------------------------------------
# Q12-α continuation chain — helper unit tests
# ---------------------------------------------------------------------------


class TestComputeRescheduleContinuation:
    """Unit tests for ``compute_reschedule_continuation`` — the chain-
    break decision helper. Issue #478 / founder ACK 2026-05-22.

    The helper takes the OLD booking + the new (service_id, visit_at)
    pair and returns ``(is_continuation, chain_break_reason, chain_root_id)``.
    Pure-function semantics so we test it without DB writes.
    """

    @pytest.fixture
    def tenant(self, db) -> Tenant:
        return Tenant.objects.create(slug="q12a", name="Q12-α Test")

    @pytest.fixture
    def chain_root(self, tenant: Tenant) -> BookingRequest:
        # Fresh ai_direct booking that anchors a chain.
        with tenant_scope(tenant):
            return BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=timezone.now() + timedelta(days=7),
                duration_min=60,
                booking_source="ai_direct",
                billable=True,
                billing_reason="ai_direct + confirmed",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
            )

    def test_first_reschedule_within_90d_same_service_is_continuation(
        self, chain_root: BookingRequest
    ) -> None:
        """Happy path: customer reschedules within 90d, no service change,
        no prior chain → continuation."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            new_visit_at=chain_root.visit_at + timedelta(days=10),
        )
        assert is_cont is True
        assert break_reason is None
        assert root_id == chain_root.id

    def test_over_90_days_breaks_chain(self, chain_root: BookingRequest) -> None:
        """Founder rule: >90d from chain root visit_at → new sale."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            new_visit_at=chain_root.visit_at + timedelta(days=91),
        )
        assert is_cont is False
        assert break_reason == "over_90d"
        assert root_id is None

    def test_exactly_90_days_inclusive_is_continuation(self, chain_root: BookingRequest) -> None:
        """Boundary: exactly 90d is still continuation. Strictly-greater
        check (>) matches founder intent «больше чем на 90 дней»."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            new_visit_at=chain_root.visit_at + timedelta(days=90),
        )
        assert is_cont is True
        assert break_reason is None

    def test_service_swap_breaks_chain(self, chain_root: BookingRequest) -> None:
        """Founder rule: strict service_id equality. Different service →
        new sale, even within 90d."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id="00000000-0000-4000-8000-000000000099",
            new_visit_at=chain_root.visit_at + timedelta(days=10),
        )
        assert is_cont is False
        assert break_reason == "service_swap"
        assert root_id is None

    def test_continuation_of_continuation_walks_to_root(
        self, tenant: Tenant, chain_root: BookingRequest
    ) -> None:
        """If the OLD row is itself a continuation (has ``original_booking_event_id``
        set), the new row's chain root is the SAME root, not the OLD row.
        90-day threshold is measured from the ROOT, not from the most
        recent reschedule. Otherwise a customer could indefinitely
        extend non-billable chains by rescheduling every 89 days."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        # Second link: 10d after root, points at root.
        with tenant_scope(tenant):
            link2 = BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=chain_root.visit_at + timedelta(days=10),
                duration_min=60,
                booking_source="ai_direct",
                billable=False,
                billing_reason="reschedule_continuation",
                original_booking_event=chain_root,
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_reschedule",
                },
            )

        # New reschedule from link2, 85d after link2's visit (= 95d after root).
        # Threshold is measured from ROOT visit_at, so this MUST break.
        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=link2,
            new_service_id=chain_root.service_id,
            new_visit_at=link2.visit_at + timedelta(days=85),
        )
        assert is_cont is False
        assert break_reason == "over_90d", (
            "threshold measured from chain root, not most-recent reschedule"
        )

    def test_continuation_chain_root_resolves_to_self_when_no_predecessor(
        self, chain_root: BookingRequest
    ) -> None:
        """When ``old.original_booking_event_id`` is NULL, the old row IS
        the chain root. The helper returns ``old.id`` as the root."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        _, _, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            new_visit_at=chain_root.visit_at + timedelta(days=1),
        )
        assert root_id == chain_root.id
