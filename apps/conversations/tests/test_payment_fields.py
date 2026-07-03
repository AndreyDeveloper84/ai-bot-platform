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


class TestPaymentEventIdempotency:
    """Schema-level sanity for ``last_payment_event_id`` — the handler-level
    idempotency tail added for Gamma's payment consumer (PR #443). Mirrors
    the ``RemoteBookingProxy.last_synced_event_id`` pattern from #442.

    NB: NOT the primary idempotency guard — that's IngestDedupe at the
    dispatcher layer. This field is forensic trace + short-circuit on
    IngestDedupe-disabled replays (testing tooling, operator manual re-fire,
    dispatcher refactor regressions).
    """

    def test_last_payment_event_id_default_empty_string(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """CharField defaults to empty string (NOT None) — blank=True default=''."""

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)
        assert conv.last_payment_event_id == ""

    def test_last_payment_event_id_accepts_ulid_format(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """26-char ULID-shaped string round-trips through save/refetch."""

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)

        ulid = "01HXYZABCDEFGHJKMNPQRSTVWXYZ"[:26]  # pragma: allowlist secret
        assert len(ulid) == 26

        conv.last_payment_event_id = ulid
        conv.save(update_fields=["last_payment_event_id"])
        conv.refresh_from_db()
        assert conv.last_payment_event_id == ulid

    def test_last_payment_event_id_accepts_uuid4_format(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """#1058/#1066: a real 36-char Ayla uuid4 round-trips through
        save/refetch — no DataError on the widened varchar(36) column.
        This is the payment.* half of the end-to-end fix (#1067 widened
        the eventbus dedupe tables; this widens the consumer's
        idempotency-tail column).

        NOTE: the DataError is Postgres-only; SQLite ignores max_length,
        so this round-trip is a real guard only on CI-Postgres. The
        backend-agnostic guard is test_last_payment_event_id_max_length_36
        (full_clean + deconstruct)."""

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)

        uuid4 = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"  # 36 chars
        assert len(uuid4) == 36

        with tenant_scope(tenant_pay):
            conv.last_payment_event_id = uuid4
            conv.save(update_fields=["last_payment_event_id"])
            conv.refresh_from_db()
            assert conv.last_payment_event_id == uuid4

    def test_last_payment_event_id_max_length_36(
        self, tenant_pay: Tenant, bot_user_pay: BotUser, settings
    ) -> None:
        """Boundary (#1066): 36 chars OK, 37 chars fails ``full_clean``."""

        from django.core.exceptions import ValidationError

        settings.STRICT_TENANT_SCOPE = "strict"
        conv = _make_conversation(tenant_pay, bot_user_pay)

        with tenant_scope(tenant_pay):
            # 36 chars — exactly at the widened limit, must round-trip.
            boundary = "a" * 36
            conv.last_payment_event_id = boundary
            conv.full_clean()
            conv.save(update_fields=["last_payment_event_id"])
            conv.refresh_from_db()
            assert conv.last_payment_event_id == boundary
            assert len(conv.last_payment_event_id) == 36

            # 37 chars — over the limit, full_clean must raise.
            conv.last_payment_event_id = "a" * 37
            with pytest.raises(ValidationError):
                conv.full_clean()

        # Surface the contract on the Field.
        field = Conversation._meta.get_field("last_payment_event_id")
        assert isinstance(field, Field)
        field_attrs = field.deconstruct()[3]
        assert field_attrs.get("max_length") == 36
        assert field_attrs.get("blank") is True
        assert field_attrs.get("default") == ""

    def test_last_payment_event_id_no_db_index(self) -> None:
        """Equality check against known event_id only — no range scan, no
        index. Documents the «no index» design decision (Gamma confirmed
        adding one would be premature).
        """

        field = Conversation._meta.get_field("last_payment_event_id")
        assert isinstance(field, Field)
        field_attrs = field.deconstruct()[3]
        assert field_attrs.get("db_index", False) is False
