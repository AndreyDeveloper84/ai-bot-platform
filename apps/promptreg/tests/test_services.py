"""publish_prompt / unpublish_prompt service tests (DRF-476 / Sprint 4 / A4)."""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.auth import get_user_model

from apps.audit.models import AuditLog
from apps.promptreg import cache as cache_mod
from apps.promptreg.models import PromptVersion
from apps.promptreg.services import publish_prompt, unpublish_prompt
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

User = get_user_model()


@pytest.fixture(autouse=True)
def _clean_cache():
    cache_mod.invalidate_all()
    yield
    cache_mod.invalidate_all()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="ps-a", name="PS-A")


@pytest.fixture
def author():
    return User.objects.create_user(username="ps-author")


def _make_version(tenant, author, *, skill="faq", version=1, is_active=False):
    return PromptVersion.all_tenants.create(
        tenant=tenant,
        skill_name=skill,
        body=f"body v{version}",
        version=version,
        created_by=author,
        is_active=is_active,
    )


class TestPublishPrompt:
    def test_activates_version_and_stamps_published_at(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v = _make_version(tenant, author, version=1)
        with tenant_scope(tenant):
            result = publish_prompt(v, user=author)
        result.refresh_from_db()
        assert result.is_active is True
        assert result.published_at is not None

    def test_deactivates_prior_active_version(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v1 = _make_version(tenant, author, version=1, is_active=True)
        v2 = _make_version(tenant, author, version=2)
        with tenant_scope(tenant):
            publish_prompt(v2, user=author)
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.is_active is False
        assert v2.is_active is True

    def test_other_skill_active_versions_untouched(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        other_skill_active = _make_version(
            tenant, author, skill="handoff", version=1, is_active=True
        )
        v = _make_version(tenant, author, skill="faq", version=1)
        with tenant_scope(tenant):
            publish_prompt(v, user=author)
        other_skill_active.refresh_from_db()
        assert other_skill_active.is_active is True  # different skill survives

    def test_audit_row_written_before_action(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v = _make_version(tenant, author, version=1)
        with tenant_scope(tenant):
            publish_prompt(v, user=author)
        # `promptreg.prompt.published` audit must exist with the right target.
        assert AuditLog.all_tenants.filter(
            tenant=tenant, action="promptreg.prompt.published", target_id=v.id
        ).exists()

    def test_pubsub_fires_on_commit(self, tenant, author, settings):
        """transaction.on_commit dispatches publish_invalidate exactly once."""

        settings.STRICT_TENANT_SCOPE = "strict"
        v = _make_version(tenant, author, version=1)
        with mock.patch.object(cache_mod, "publish_invalidate") as mocked:
            with tenant_scope(tenant):
                publish_prompt(v, user=author)
        # transaction=True fixture means atomic blocks commit + fire on_commit.
        mocked.assert_called_once_with(tenant.id, "prompt", ("faq",))

    def test_missing_tenant_context_raises(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        v = _make_version(tenant, author, version=1)
        with pytest.raises(ValueError, match="tenant in scope"):
            publish_prompt(v, user=author)

    def test_cross_tenant_write_rejected(self, tenant, author, settings):
        other = Tenant.objects.create(slug="ps-b", name="PS-B")
        settings.STRICT_TENANT_SCOPE = "strict"
        v = _make_version(tenant, author, version=1)
        with tenant_scope(other):
            with pytest.raises(ValueError, match="cross-tenant"):
                publish_prompt(v, user=author)


class TestUnpublishPrompt:
    def test_flips_is_active_false(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v = _make_version(tenant, author, version=1)
        with tenant_scope(tenant):
            publish_prompt(v, user=author)
            unpublish_prompt(v, user=author)
        v.refresh_from_db()
        assert v.is_active is False

    def test_noop_on_already_inactive(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v = _make_version(tenant, author, version=1)  # already inactive
        with tenant_scope(tenant):
            unpublish_prompt(v, user=author)
        # Audit row for the noop variant is written.
        assert AuditLog.all_tenants.filter(
            tenant=tenant, action="promptreg.prompt.unpublish_noop", target_id=v.id
        ).exists()

    def test_emits_unpublished_audit(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v = _make_version(tenant, author, version=1)
        with tenant_scope(tenant):
            publish_prompt(v, user=author)
            unpublish_prompt(v, user=author)
        assert AuditLog.all_tenants.filter(
            tenant=tenant, action="promptreg.prompt.unpublished", target_id=v.id
        ).exists()

    def test_pubsub_fires_on_commit(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        v = _make_version(tenant, author, version=1)
        with tenant_scope(tenant):
            publish_prompt(v, user=author)
        with mock.patch.object(cache_mod, "publish_invalidate") as mocked:
            with tenant_scope(tenant):
                unpublish_prompt(v, user=author)
        mocked.assert_called_once_with(tenant.id, "prompt", ("faq",))
