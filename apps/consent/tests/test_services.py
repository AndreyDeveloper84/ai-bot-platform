"""ConsentRecord services tests (DRF-457 / Sprint 3 / A2)."""

from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.consent.models import ConsentRecord
from apps.consent.services import get_consents, grant, has_consent, withdraw
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="consent-a", name="A")


@pytest.fixture
def tenant_b() -> Tenant:
    return Tenant.objects.create(slug="consent-b", name="B")


@pytest.fixture
def bot_user_a(tenant_a) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_a, channel="max", channel_user_id="consent-a-bot"
    )


@pytest.fixture
def bot_user_b(tenant_b) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_b, channel="max", channel_user_id="consent-b-bot"
    )


class TestGrant:
    def test_grant_creates_active_record(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            record = grant(
                bot_user_a,
                consent_type="personal_data",
                source="implicit_first_msg",
                document_version="privacy-v1.0",
            )
        record.refresh_from_db()
        assert record.granted is True
        assert record.consent_type == "personal_data"
        assert record.document_version == "privacy-v1.0"
        assert record.withdrawn_at is None

    def test_grant_emits_event_and_audit_after_commit(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            grant(bot_user_a, consent_type="marketing", source="bot_message:abc")

        events = list(Event.objects.filter(tenant=tenant_a).values_list("event_type", flat=True))
        assert "consent_granted" in events

        audits = list(AuditLog.all_tenants.filter(tenant=tenant_a).values_list("action", flat=True))
        assert "consent.granted" in audits

    def test_grant_payload_does_not_leak_pii(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        # Set raw PII on the bot_user.
        bot_user_a.phone = "+79991234567"
        bot_user_a.save(update_fields=["phone"])
        with tenant_scope(tenant_a):
            grant(bot_user_a, consent_type="personal_data", source="reg_form")
        ev = Event.objects.get(tenant=tenant_a, event_type="consent_granted")
        # Phone NEVER in payload, only the consent_type and bot_user_id.
        assert "+79991234567" not in str(ev.payload)
        assert ev.payload["consent_type"] == "personal_data"


class TestHasConsent:
    def test_grant_then_has_consent_true(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            grant(bot_user_a, consent_type="marketing", source="opt-in-flow")
            assert has_consent(bot_user_a, "marketing") is True

    def test_no_grant_then_has_consent_false(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            assert has_consent(bot_user_a, "marketing") is False

    def test_document_version_mismatch_returns_false(self, tenant_a, bot_user_a, settings):
        """Privacy-policy bump invalidates old consents → re-prompt path."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            grant(
                bot_user_a,
                consent_type="personal_data",
                source="reg_form",
                document_version="privacy-v1.0",
            )
            # User has v1.0 consent.
            assert has_consent(bot_user_a, "personal_data", document_version="privacy-v1.0") is True
            # Operator publishes v2.0 — the v1.0 row no longer counts.
            assert (
                has_consent(bot_user_a, "personal_data", document_version="privacy-v2.0") is False
            )


class TestWithdraw:
    def test_withdraw_stamps_withdrawn_at_does_not_delete(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            granted = grant(bot_user_a, consent_type="marketing", source="opt-in")
            withdrawn = withdraw(bot_user_a, consent_type="marketing", source="opt-out")

        # Same row, with withdrawn_at now set.
        assert withdrawn is not None
        assert withdrawn.id == granted.id
        assert withdrawn.withdrawn_at is not None
        # And has_consent now False.
        with tenant_scope(tenant_a):
            assert has_consent(bot_user_a, "marketing") is False

        # Row NOT deleted — audit trail preserved.
        assert ConsentRecord.all_tenants.filter(pk=granted.pk).exists()

    def test_withdraw_when_no_active_consent_returns_none(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            result = withdraw(bot_user_a, consent_type="marketing", source="opt-out")
        assert result is None
        # Audit row written even on no-op.
        assert AuditLog.all_tenants.filter(action="consent.withdraw_noop").exists()

    def test_grant_after_withdraw_creates_new_row(self, tenant_a, bot_user_a, settings):
        """User changes mind → 3 ConsentRecord rows: granted, withdrawn, re-granted."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            first = grant(bot_user_a, consent_type="marketing", source="opt-in-1")
            withdraw(bot_user_a, consent_type="marketing", source="changed-mind")
            second = grant(bot_user_a, consent_type="marketing", source="opt-in-2")

        # Two physical rows (withdraw stamped the first, didn't create a new).
        rows = ConsentRecord.all_tenants.filter(
            bot_user=bot_user_a, consent_type="marketing"
        ).order_by("captured_at")
        assert rows.count() == 2
        assert rows.first().id == first.id
        assert rows.first().withdrawn_at is not None
        assert rows.last().id == second.id
        assert rows.last().withdrawn_at is None

        # Latest grant active → has_consent True again.
        with tenant_scope(tenant_a):
            assert has_consent(bot_user_a, "marketing") is True


class TestGetConsents:
    def test_returns_only_active(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            grant(bot_user_a, consent_type="personal_data", source="reg")
            grant(bot_user_a, consent_type="marketing", source="opt-in")
            withdraw(bot_user_a, consent_type="marketing", source="opt-out")
            active = get_consents(bot_user_a)
        types = {c.consent_type for c in active}
        assert types == {"personal_data"}


class TestCrossTenantIsolation:
    def test_grant_in_tenant_a_invisible_in_tenant_b(
        self, tenant_a, tenant_b, bot_user_a, bot_user_b, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            grant(bot_user_a, consent_type="personal_data", source="reg")
        # bot_user_b in tenant_b — separate consent space.
        with tenant_scope(tenant_b):
            assert has_consent(bot_user_b, "personal_data") is False

    def test_missing_tenant_context_raises(self, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        with pytest.raises(ValueError, match="tenant in scope"):
            grant(bot_user_a, consent_type="marketing", source="x")
        with pytest.raises(ValueError, match="tenant in scope"):
            has_consent(bot_user_a, "marketing")
