"""Registry API tests (DRF-481 / Sprint 4 / A6)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.promptreg import cache as cache_mod
from apps.promptreg.models import DisclaimerLibrary, PromptVersion, ThresholdConfig
from apps.promptreg.registry import get_disclaimer, get_prompt, get_threshold
from apps.promptreg.services import publish_prompt
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

User = get_user_model()


@pytest.fixture(autouse=True)
def _clean_cache(settings):
    settings.PROMPTREG_DISABLE_SUBSCRIBER = True
    cache_mod.invalidate_all()
    yield
    cache_mod.invalidate_all()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="reg-a", name="REG-A")


@pytest.fixture
def author():
    return User.objects.create_user(username="reg-author")


class TestGetPrompt:
    def test_returns_active_version(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v = PromptVersion.all_tenants.create(
            tenant=tenant,
            skill_name="faq",
            body="hello",
            version=1,
            created_by=author,
            is_active=True,
        )
        result = get_prompt(tenant, "faq")
        assert result.id == v.id

    def test_raises_when_no_active(self, tenant):
        with pytest.raises(PromptVersion.DoesNotExist):
            get_prompt(tenant, "missing")

    def test_cache_hit_avoids_db(self, tenant, author, settings, django_assert_num_queries):
        settings.STRICT_TENANT_SCOPE = "strict"
        PromptVersion.all_tenants.create(
            tenant=tenant,
            skill_name="faq",
            body="v1",
            version=1,
            created_by=author,
            is_active=True,
        )
        # First call: 1 query.
        with django_assert_num_queries(1):
            get_prompt(tenant, "faq")
        # Second call: 0 queries (cache hit).
        with django_assert_num_queries(0):
            get_prompt(tenant, "faq")

    def test_publish_invalidates_cache(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v1 = PromptVersion.all_tenants.create(
            tenant=tenant,
            skill_name="faq",
            body="v1-body",
            version=1,
            created_by=author,
            is_active=True,
        )
        cache_mod.put_prompt(tenant.id, "faq", v1)
        # Publish v2 — `transaction.on_commit` should fire pub/sub which
        # evicts cache via _dispatch_invalidate_sync hook in tests.
        v2 = PromptVersion.all_tenants.create(
            tenant=tenant,
            skill_name="faq",
            body="v2-body",
            version=2,
            created_by=author,
            is_active=False,
        )
        with tenant_scope(tenant):
            publish_prompt(v2, user=author)
        # Simulate the subscriber-side eviction (production: Redis pub/sub).
        cache_mod._dispatch_invalidate_sync(tenant.id, "prompt", ("faq",))
        # Next get returns v2.
        assert get_prompt(tenant, "faq").body == "v2-body"


class TestGetThreshold:
    def test_exact_match(self, tenant):
        ThresholdConfig.all_tenants.create(
            tenant=tenant, key="intent_min", applied_to="faq", value=Decimal("0.65")
        )
        assert get_threshold(tenant, "intent_min", applied_to="faq") == Decimal("0.65")

    def test_global_fallback(self, tenant):
        ThresholdConfig.all_tenants.create(
            tenant=tenant, key="intent_min", applied_to="", value=Decimal("0.5")
        )
        # applied_to="faq" not present → falls back to global "".
        assert get_threshold(tenant, "intent_min", applied_to="faq") == Decimal("0.5")

    def test_skill_specific_wins_over_global(self, tenant):
        ThresholdConfig.all_tenants.create(
            tenant=tenant, key="intent_min", applied_to="", value=Decimal("0.5")
        )
        ThresholdConfig.all_tenants.create(
            tenant=tenant, key="intent_min", applied_to="faq", value=Decimal("0.8")
        )
        assert get_threshold(tenant, "intent_min", applied_to="faq") == Decimal("0.8")

    def test_raises_when_missing(self, tenant):
        with pytest.raises(KeyError):
            get_threshold(tenant, "no_such_key")

    def test_cache_hit_avoids_db(self, tenant, django_assert_num_queries):
        ThresholdConfig.all_tenants.create(
            tenant=tenant, key="k", applied_to="", value=Decimal("0.7")
        )
        with django_assert_num_queries(1):
            get_threshold(tenant, "k")
        with django_assert_num_queries(0):
            get_threshold(tenant, "k")


class TestGetDisclaimer:
    def test_returns_active(self, tenant):
        DisclaimerLibrary.all_tenants.create(
            tenant=tenant,
            category="medical",
            risk_level="high",
            text="Consult a doctor.",
        )
        assert "doctor" in get_disclaimer(tenant, "medical", "high")

    def test_raises_when_missing(self, tenant):
        with pytest.raises(DisclaimerLibrary.DoesNotExist):
            get_disclaimer(tenant, "general", "low")

    def test_withdrawn_disclaimers_excluded(self, tenant):
        from django.utils import timezone

        d = DisclaimerLibrary.all_tenants.create(
            tenant=tenant, category="general", risk_level="low", text="old"
        )
        d.withdrawn_at = timezone.now()
        d.save(update_fields=["withdrawn_at"])
        with pytest.raises(DisclaimerLibrary.DoesNotExist):
            get_disclaimer(tenant, "general", "low")

    def test_cache_hit_avoids_db(self, tenant, django_assert_num_queries):
        DisclaimerLibrary.all_tenants.create(
            tenant=tenant, category="general", risk_level="low", text="hi"
        )
        with django_assert_num_queries(1):
            get_disclaimer(tenant, "general", "low")
        with django_assert_num_queries(0):
            get_disclaimer(tenant, "general", "low")
