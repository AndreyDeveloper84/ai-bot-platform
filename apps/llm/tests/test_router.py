"""LLMRouter tests (DRF-587 / Sprint 7 / L5)."""

from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.llm.protocol import LLMProvider
from apps.llm.router import (
    EVENT_PROVIDER_RESOLVED,
    LLMRouter,
    get_router,
    reset_router_cache,
)
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _isolated_router(settings: pytest.FixtureRequest):
    settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
    settings.SKILL_LLM_PROVIDER = {}  # type: ignore[attr-defined]
    reset_router_cache()
    yield
    reset_router_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="llm-router", name="LLM Router")


# ---------------------------------------------------------------------------
# Tier 1 — per-tenant
# ---------------------------------------------------------------------------


class TestTenantFeature:
    def test_tenant_feature_overrides_all(
        self, tenant: Tenant, settings: pytest.FixtureRequest
    ) -> None:
        settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
        settings.SKILL_LLM_PROVIDER = {"faq": "openai"}  # type: ignore[attr-defined]
        tenant.features = {"llm_provider": "anthropic"}
        tenant.save()

        router = LLMRouter()
        provider = router.get_provider(tenant, skill="faq")
        assert provider.name == "anthropic"  # type: ignore[attr-defined]

    def test_invalid_tenant_value_falls_through(
        self, tenant: Tenant, settings: pytest.FixtureRequest
    ) -> None:
        settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
        tenant.features = {"llm_provider": "garbage_value"}
        tenant.save()
        provider = LLMRouter().get_provider(tenant, skill="faq")
        # Falls through to org default.
        assert provider.name == "openai"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tier 2 — per-skill
# ---------------------------------------------------------------------------


class TestSkillDefault:
    def test_skill_default_used_when_no_tenant_feature(
        self, tenant: Tenant, settings: pytest.FixtureRequest
    ) -> None:
        settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
        settings.SKILL_LLM_PROVIDER = {"intent": "anthropic"}  # type: ignore[attr-defined]
        tenant.features = {}
        tenant.save()

        provider = LLMRouter().get_provider(tenant, skill="intent")
        assert provider.name == "anthropic"  # type: ignore[attr-defined]

    def test_missing_skill_falls_through_to_org(self, settings: pytest.FixtureRequest) -> None:
        settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
        settings.SKILL_LLM_PROVIDER = {"intent": "anthropic"}  # type: ignore[attr-defined]
        provider = LLMRouter().get_provider(skill="not_in_map")
        assert provider.name == "openai"  # type: ignore[attr-defined]

    def test_invalid_skill_value_falls_through(self, settings: pytest.FixtureRequest) -> None:
        settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
        settings.SKILL_LLM_PROVIDER = {"faq": "weird"}  # type: ignore[attr-defined]
        provider = LLMRouter().get_provider(skill="faq")
        assert provider.name == "openai"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tier 3 — org-wide default
# ---------------------------------------------------------------------------


class TestOrgDefault:
    def test_no_tenant_no_skill(self, settings: pytest.FixtureRequest) -> None:
        settings.LLM_PROVIDER = "anthropic"  # type: ignore[attr-defined]
        provider = LLMRouter().get_provider()
        assert provider.name == "anthropic"  # type: ignore[attr-defined]

    def test_bad_org_default_falls_back_to_openai(self, settings: pytest.FixtureRequest) -> None:
        settings.LLM_PROVIDER = "unknown_vendor"  # type: ignore[attr-defined]
        provider = LLMRouter().get_provider()
        assert provider.name == "openai"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Embedding fallback
# ---------------------------------------------------------------------------


class TestEmbeddingFallback:
    def test_anthropic_embedding_falls_back_to_openai(self, tenant: Tenant) -> None:
        tenant.features = {"llm_provider": "anthropic"}
        tenant.save()
        provider = LLMRouter().get_provider(tenant, skill="faq", op="embedding")
        # Anthropic can't embed → router swaps to OpenAI.
        assert provider.name == "openai"  # type: ignore[attr-defined]

    def test_openai_embedding_no_fallback(self, tenant: Tenant) -> None:
        provider = LLMRouter().get_provider(tenant, op="embedding")
        # OpenAI is the org default + supports embeddings → no fallback.
        assert provider.name == "openai"  # type: ignore[attr-defined]

    def test_anthropic_complete_keeps_anthropic(self, tenant: Tenant) -> None:
        tenant.features = {"llm_provider": "anthropic"}
        tenant.save()
        provider = LLMRouter().get_provider(tenant, op="complete")
        # op != embedding → no fallback; Anthropic stands.
        assert provider.name == "anthropic"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Quota fallback
# ---------------------------------------------------------------------------


class TestQuotaFallback:
    def test_quota_fallback_swaps_provider(self) -> None:
        # Org default = openai. Caller asks for fallback away from openai.
        provider = LLMRouter().get_provider(prefer_fallback_from="openai")
        assert provider.name == "anthropic"  # type: ignore[attr-defined]

    def test_quota_fallback_from_anthropic(self, settings: pytest.FixtureRequest) -> None:
        settings.LLM_PROVIDER = "anthropic"  # type: ignore[attr-defined]
        provider = LLMRouter().get_provider(prefer_fallback_from="anthropic")
        assert provider.name == "openai"  # type: ignore[attr-defined]

    def test_quota_fallback_no_op_when_candidate_already_different(
        self, settings: pytest.FixtureRequest
    ) -> None:
        settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
        # prefer_fallback_from='anthropic' but candidate is already openai —
        # router doesn't swap (no point).
        provider = LLMRouter().get_provider(prefer_fallback_from="anthropic")
        assert provider.name == "openai"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_audit_row_per_resolution(self, tenant: Tenant, db) -> None:
        LLMRouter().get_provider(tenant, skill="faq")
        rows = AuditLog.all_tenants.filter(action=EVENT_PROVIDER_RESOLVED)
        assert rows.exists()
        row = rows.first()
        assert row is not None
        assert row.payload["chosen_provider"] == "openai"
        assert row.payload["source"] == "org_default"

    def test_audit_records_source_tenant_feature(self, tenant: Tenant, db) -> None:
        tenant.features = {"llm_provider": "anthropic"}
        tenant.save()
        LLMRouter().get_provider(tenant, skill="faq")
        rows = AuditLog.all_tenants.filter(action=EVENT_PROVIDER_RESOLVED).order_by("-created_at")
        row = rows.first()
        assert row is not None
        assert row.payload["source"] == "tenant_feature"
        assert row.payload["chosen_provider"] == "anthropic"

    def test_audit_records_source_embedding_fallback(self, tenant: Tenant, db) -> None:
        tenant.features = {"llm_provider": "anthropic"}
        tenant.save()
        LLMRouter().get_provider(tenant, skill="faq", op="embedding")
        rows = AuditLog.all_tenants.filter(action=EVENT_PROVIDER_RESOLVED).order_by("-created_at")
        row = rows.first()
        assert row is not None
        assert row.payload["source"] == "embedding_fallback"
        assert row.payload["chosen_provider"] == "openai"

    def test_audit_records_source_quota_fallback(self, db) -> None:
        LLMRouter().get_provider(prefer_fallback_from="openai")
        rows = AuditLog.all_tenants.filter(action=EVENT_PROVIDER_RESOLVED).order_by("-created_at")
        row = rows.first()
        assert row is not None
        assert row.payload["source"] == "quota_fallback"


# ---------------------------------------------------------------------------
# Provider caching
# ---------------------------------------------------------------------------


class TestProviderCaching:
    def test_same_router_returns_same_provider_instance(self) -> None:
        router = LLMRouter()
        a = router.get_provider()
        b = router.get_provider()
        # Cached — same Python id.
        assert a is b

    def test_get_router_singleton(self) -> None:
        assert get_router() is get_router()


# ---------------------------------------------------------------------------
# Protocol conformance of routed providers
# ---------------------------------------------------------------------------


class TestRoutedConformance:
    def test_routed_provider_implements_protocol(self, tenant: Tenant) -> None:
        provider = LLMRouter().get_provider(tenant)
        assert isinstance(provider, LLMProvider)


# ---------------------------------------------------------------------------
# LLM retro hotfix coverage
# ---------------------------------------------------------------------------


class TestRetroB1ProviderInitTypedException:
    """Pre-fix LLMRouter._load_provider surfaced raw Exception from the
    provider constructor. Callers had to wrap router lookups in bare
    try/except Exception (see apps/skills/booking/skill.py hotfix #8).
    Post-fix re-raises as LLMProviderUnavailable so callers handle
    uniformly and Sentry pinpoints config errors via chained traceback.
    """

    def test_constructor_failure_raises_typed_exception(self, tenant: Tenant, monkeypatch) -> None:
        from apps.llm.protocol import LLMProviderUnavailable

        # Force the OpenAI provider constructor to raise.
        def _boom(self, *args, **kwargs) -> None:
            raise RuntimeError("OPENAI_API_KEY not configured")

        monkeypatch.setattr(
            "apps.llm.providers.openai_provider.OpenAIProvider.__init__",
            _boom,
        )

        router = LLMRouter()
        with pytest.raises(LLMProviderUnavailable, match="openai.*failed to initialise"):
            router.get_provider(tenant)


class TestRetroY7PricingCoverage:
    """Drift between router-resolved model names and the pricing table
    silently raises UnknownModelError at first use. The validator
    catches the drift at CI time.
    """

    def test_default_models_all_priced(self) -> None:
        from apps.llm.pricing import validate_pricing_coverage

        missing = validate_pricing_coverage()
        assert missing == [], (
            f"models referenced by the router but missing from MODEL_PRICES: {missing}"
        )
