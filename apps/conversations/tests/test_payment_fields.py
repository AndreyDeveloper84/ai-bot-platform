"""Tests for Conversation payment-event grounding fields.

Schema-level tests only — these fields are POPULATED by Gamma's
payment consumer (PR #443) from Ayla canonical payment.* events
per event-contract.md §3.5-§3.8. This PR ships the schema only.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.db.models import Field
from django.utils import timezone

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_pay() -> Tenant:
    return Tenant.objects.create(slug="conv-pay", name="Pay")


@pytest.fixture
def bot_user_pay(tenant_pay: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_pay, channel="max", channel_user_id="conv-pay-bot"
    )


def _make_conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    with tenant_scope(tenant):
        return Conversation.objects.create(tenant=tenant, bot_user=bot_user)


class TestPaymentFields:
    """Schema-level sanity for the 6 payment grounding fields."""

    def test_payment_datetime_fields_default_none(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """All four DateTimeField / UUID payment fields default to None."""

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)
        assert conv.last_payment_captured_at is None
        assert conv.last_payment_failed_at is None
        assert conv.last_payment_refunded_at is None
        assert conv.pending_payment_id is None

    def test_failure_code_default_empty_string(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """CharField defaults to empty string (NOT None) — blank=True default=''."""

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)
        assert conv.last_payment_failure_code == ""

    def test_consecutive_failures_default_zero(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """PositiveSmallIntegerField counter starts at 0."""

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)
        assert conv.consecutive_payment_failures == 0

    def test_failure_code_accepts_max_32_chars(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """Boundary: max_length=32. The longest documented enum value
        ('three_d_secure_failed' = 21 chars) fits comfortably; a 32-char
        synthetic value is the explicit boundary the field declares.
        """

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)

        # Documented enum value — must fit.
        conv.last_payment_failure_code = "three_d_secure_failed"
        conv.save(update_fields=["last_payment_failure_code"])
        conv.refresh_from_db()
        assert conv.last_payment_failure_code == "three_d_secure_failed"

        # 32-char boundary — exactly at the limit.
        boundary = "a" * 32
        conv.last_payment_failure_code = boundary
        conv.save(update_fields=["last_payment_failure_code"])
        conv.refresh_from_db()
        assert conv.last_payment_failure_code == boundary
        assert len(conv.last_payment_failure_code) == 32

        # The Field declares max_length=32; surface the contract.
        field = Conversation._meta.get_field("last_payment_failure_code")
        assert isinstance(field, Field)
        field_attrs = field.deconstruct()[3]
        assert field_attrs.get("max_length") == 32
        assert field_attrs.get("blank") is True
        assert field_attrs.get("default") == ""

    def test_pending_payment_id_indexed(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """db_index=True ensures we can query by pending_payment_id efficiently."""

        field = Conversation._meta.get_field("pending_payment_id")
        assert isinstance(field, Field)
        field_attrs = field.deconstruct()[3]
        assert field_attrs.get("db_index") is True
        assert field_attrs.get("null") is True
        assert field_attrs.get("blank") is True

    def test_payment_field_writes_persist(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """Smoke: write all 6 fields, refetch, verify round-trip."""

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)

        captured_at = timezone.now()
        failed_at = timezone.now() - dt.timedelta(hours=1)
        refunded_at = timezone.now() - dt.timedelta(days=2)
        pending_id = uuid.uuid4()

        conv.last_payment_captured_at = captured_at
        conv.last_payment_failed_at = failed_at
        conv.last_payment_failure_code = "insufficient_funds"
        conv.last_payment_refunded_at = refunded_at
        conv.pending_payment_id = pending_id
        conv.consecutive_payment_failures = 3
        conv.save(
            update_fields=[
                "last_payment_captured_at",
                "last_payment_failed_at",
                "last_payment_failure_code",
                "last_payment_refunded_at",
                "pending_payment_id",
                "consecutive_payment_failures",
            ]
        )

        conv.refresh_from_db()
        assert conv.last_payment_captured_at == captured_at
        assert conv.last_payment_failed_at == failed_at
        assert conv.last_payment_failure_code == "insufficient_funds"
        assert conv.last_payment_refunded_at == refunded_at
        assert conv.pending_payment_id == pending_id
        assert conv.consecutive_payment_failures == 3

    def test_datetime_fields_not_indexed_by_default(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """The three datetime payment fields are NOT individually indexed —
        unlike ``last_booking_at`` and ``pending_payment_id``, they back AI
        context grounding (read on the same row already fetched by id /
        bot_user lookup), not by-recency cohort queries. Keep this contract
        explicit so a future drift toward extra indexes is intentional.
        """

        for name in (
            "last_payment_captured_at",
            "last_payment_failed_at",
            "last_payment_refunded_at",
        ):
            field = Conversation._meta.get_field(name)
            # ``get_field`` is typed as ``Field | ForeignObjectRel``; these
            # are concrete columns, narrow for mypy.
            assert isinstance(field, Field)
            field_attrs = field.deconstruct()[3]
            assert field_attrs.get("db_index", False) is False, name
            assert field_attrs.get("null") is True, name
            assert field_attrs.get("blank") is True, name
