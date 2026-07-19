"""Memory consent gate tests (M-B3 / #1100), aligned to ADR-0011 §11.

Green-zone memory writes ride the general PERSONAL_DATA welcome consent
(#1046). The Memory Foundation runs on the GLOBAL (sentinel-tenant) bot where
``current_tenant()`` is None by design, so the gate reads consent via the
tenant-less :func:`has_global_consent` path (mirror of the #1074 write path).
"""

from __future__ import annotations

import pytest

from apps.consent.memory import can_store_green_memory
from apps.consent.models import ConsentRecord
from apps.consent.services import has_global_consent, record_global_consent
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.tenancy.context import current_tenant

pytestmark = pytest.mark.django_db(transaction=True)


def _global_bot_user(uid: str = "mb3-1") -> BotUser:
    """A global (sentinel-tenant) BotUser — the memory-foundation identity."""

    return resolve_or_create_global_bot_user(channel="max", channel_user_id=uid)


class TestCanStoreGreenMemory:
    def test_false_without_personal_data_consent(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _global_bot_user("mb3-green-none")
        assert can_store_green_memory(bu) is False
        assert current_tenant() is None  # never entered a tenant scope

    def test_true_after_welcome_personal_data_consent(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _global_bot_user("mb3-green-ok")
        # The welcome flow (#1046) grants PERSONAL_DATA on the global path.
        record_global_consent(bu, source="global_onboarding:welcome_s2")
        assert can_store_green_memory(bu) is True

    def test_reflects_withdrawal(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _global_bot_user("mb3-withdraw")
        rec = record_global_consent(bu, source="welcome")
        assert can_store_green_memory(bu) is True
        # Simulate withdrawal (stamp withdrawn_at directly — global path).
        from django.utils import timezone

        ConsentRecord.all_tenants.filter(pk=rec.pk).update(withdrawn_at=timezone.now())
        assert can_store_green_memory(bu) is False

    def test_other_consent_type_does_not_unlock_green(self, settings):
        # Green rides PERSONAL_DATA specifically — an unrelated grant (MARKETING)
        # must NOT unlock green memory writes.
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _global_bot_user("mb3-marketing-only")
        record_global_consent(
            bu,
            consent_type=ConsentRecord.ConsentType.MARKETING.value,
            source="opt-in",
        )
        assert can_store_green_memory(bu) is False

    def test_isolated_per_user(self, settings):
        # One global user's PERSONAL_DATA consent must not unlock another's.
        settings.STRICT_TENANT_SCOPE = "strict"
        consenter = _global_bot_user("mb3-iso-yes")
        other = _global_bot_user("mb3-iso-no")
        record_global_consent(consenter, source="welcome")
        assert can_store_green_memory(consenter) is True
        assert can_store_green_memory(other) is False


class TestHasGlobalConsent:
    def test_reads_tenant_less_without_scope(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _global_bot_user("mb3-hgc")
        assert has_global_consent(bu, ConsentRecord.ConsentType.PERSONAL_DATA.value) is False
        record_global_consent(bu, source="welcome")
        assert has_global_consent(bu, ConsentRecord.ConsentType.PERSONAL_DATA.value) is True
        assert current_tenant() is None

    def test_document_version_match(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _global_bot_user("mb3-docver")
        record_global_consent(bu, source="welcome", document_version="privacy-v1.0")
        pd = ConsentRecord.ConsentType.PERSONAL_DATA.value
        assert has_global_consent(bu, pd, document_version="privacy-v1.0") is True
        # A policy bump invalidates the old-version grant.
        assert has_global_consent(bu, pd, document_version="privacy-v2.0") is False
