"""customer.consent.changed auto-wire (Phase 2.2 PR-F).

Tests that apps.consent.services grant/withdraw co-emit to the
domain bus alongside the existing analytics emit + audit row.
"""

from __future__ import annotations

import pytest

from apps.consent.services import grant, withdraw
from apps.eventbus import vocabulary as V
from apps.eventbus.models import DomainEvent
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="evbus-consent", name="EvbusConsent")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="evbus-consent-bot"
    )


class TestGrantEmitsDomainEvent:
    def test_grant_emits_customer_consent_changed_granted_true(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            grant(bot_user, consent_type="personal_data", source="reg_form")

        rows = DomainEvent.objects.filter(event_name=V.CUSTOMER_CONSENT_CHANGED)
        assert rows.count() == 1
        ev = rows.first()
        assert ev.data["customer_id"] == str(bot_user.id)
        assert ev.data["consent_type"] == "personal_data"
        assert ev.data["granted"] is True
        assert ev.data["granted_at"]  # ISO timestamp populated
        assert ev.data["granted_via"] == "reg_form"
        assert ev.tenant_id == tenant.id
        # Customer-actor envelope per default for emit_customer_consent_changed.
        assert ev.actor["type"] == "human"
        assert ev.actor["role"] == "customer"
        assert ev.actor["id"] == str(bot_user.id)

    def test_grant_pii_not_leaked_to_domain_bus(self, tenant, bot_user, settings):
        # The taxonomy §6 PII rule already rejects phone-shaped strings
        # in `data`/`metadata`. Confirm that grant's source field is
        # truncated/safe.
        settings.STRICT_TENANT_SCOPE = "strict"
        bot_user.phone = "+79991234567"
        bot_user.save(update_fields=["phone"])
        with tenant_scope(tenant):
            grant(bot_user, consent_type="personal_data", source="bot_message:abc")
        ev = DomainEvent.objects.get(event_name=V.CUSTOMER_CONSENT_CHANGED)
        # Phone never appears anywhere.
        assert "+79991234567" not in str(ev.data)
        assert "+79991234567" not in str(ev.actor)


class TestWithdrawEmitsDomainEvent:
    def test_withdraw_emits_customer_consent_changed_granted_false(
        self, tenant, bot_user, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            grant(bot_user, consent_type="marketing", source="reg_form")
            withdraw(bot_user, consent_type="marketing", source="user_opt_out")

        rows = DomainEvent.objects.filter(event_name=V.CUSTOMER_CONSENT_CHANGED).order_by(
            "occurred_at"
        )
        assert rows.count() == 2
        grant_ev, withdraw_ev = rows
        assert grant_ev.data["granted"] is True
        assert withdraw_ev.data["granted"] is False
        assert withdraw_ev.data["granted_via"] == "user_opt_out"

    def test_withdraw_noop_does_not_emit(self, tenant, bot_user, settings):
        # No prior grant → withdraw is a no-op (services.withdraw returns
        # None). Domain bus must NOT see a customer.consent.changed.
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            result = withdraw(bot_user, consent_type="marketing", source="opt_out")
        assert result is None
        assert not DomainEvent.objects.filter(event_name=V.CUSTOMER_CONSENT_CHANGED).exists()


class TestBothBusesCoexist:
    def test_grant_emits_to_analytics_and_domain_buses(self, tenant, bot_user, settings):
        # Two-bus contract — apps/events/ analytics + apps/eventbus/ domain
        # fire from the same grant() call. Neither swallows the other.
        from apps.events.models import Event

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            grant(bot_user, consent_type="health", source="reg_form")

        # Analytics: snake_case `consent_granted`.
        analytics = Event.objects.filter(event_type="consent_granted", tenant=tenant)
        assert analytics.count() == 1

        # Domain: dot.notation `customer.consent.changed`.
        domain = DomainEvent.objects.filter(event_name=V.CUSTOMER_CONSENT_CHANGED, tenant=tenant)
        assert domain.count() == 1
