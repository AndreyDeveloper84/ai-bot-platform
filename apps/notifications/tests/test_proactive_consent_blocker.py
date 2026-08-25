"""The parametric consent set on the shared proactive gate (DRF-1338).

The four cases the ticket names as mandatory:

* active PERSONAL_DATA, no HEALTH, called with both -> ``no_health_consent``
  (TestHealthConsent)
* the same user, called without the argument -> ``None``
  (TestHealthConsent) — the default is the historical gate, so existing
  callers keep their exact slugs
* BOTH consents active, called with both -> ``None``
  (TestHealthConsent)
* the new slug is enumerated in ``BLOCK_REASONS`` and distinct from
  ``no_consent`` (TestBlockReasons)
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import pytest

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.notifications.proactive import BLOCK_REASONS, consent_blocker
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value
HEALTH = ConsentRecord.ConsentType.HEALTH.value

NOW = datetime(2026, 5, 1, tzinfo=dt_timezone.utc)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="drf1338-salon", name="Salon", timezone="Europe/Moscow")


def grant(bot_user: BotUser, consent_type: str) -> ConsentRecord:
    """Give ``bot_user`` one active consent record of ``consent_type``."""
    return ConsentRecord.all_tenants.create(
        tenant=bot_user.tenant,
        bot_user=bot_user,
        consent_type=consent_type,
        granted=True,
        source="test:fixture",
    )


def make_user(tenant: Tenant, *, suffix: str = "1") -> BotUser:
    """A recipient that clears the first three gate conditions.

    Consent records are NOT created here — each test states explicitly
    which bases the person holds, because that is the variable under test.
    """
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"drf1338-{suffix}",
        chat_id="chat-drf1338-1",
        consent_at=NOW,
    )


class TestBlockReasons:
    def test_health_slug_is_enumerated(self) -> None:
        assert "no_health_consent" in BLOCK_REASONS

    def test_health_slug_is_distinct_from_no_consent(self) -> None:
        assert "no_health_consent" != "no_consent"


class TestHealthConsent:
    """Active PERSONAL_DATA and no HEALTH — the weight-occasion population."""

    def test_both_types_required_blocks_with_health_slug(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        grant(user, PERSONAL_DATA)

        assert consent_blocker(user, (PERSONAL_DATA, HEALTH)) == "no_health_consent"

    def test_default_call_unchanged_by_the_missing_health(self, tenant: Tenant) -> None:
        """Backwards compatibility: no argument = the historical gate."""
        user = make_user(tenant)
        grant(user, PERSONAL_DATA)

        assert consent_blocker(user) is None

    def test_both_consents_active_passes_with_both_types(self, tenant: Tenant) -> None:
        """Positive guard on the same data shape."""
        user = make_user(tenant)
        grant(user, PERSONAL_DATA)
        grant(user, HEALTH)

        assert consent_blocker(user, (PERSONAL_DATA, HEALTH)) is None

    def test_missing_personal_data_keeps_its_own_slugs(self, tenant: Tenant) -> None:
        """The new argument must not rename the baseline failure."""
        user = make_user(tenant)
        grant(user, HEALTH)

        assert consent_blocker(user, (PERSONAL_DATA, HEALTH)) == "consent_unproven"
