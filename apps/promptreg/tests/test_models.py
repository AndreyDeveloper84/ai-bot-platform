"""PromptVersion model tests (DRF-477 / Sprint 4 / A1)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

from apps.promptreg.models import PromptVersion
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="pv-a", name="PV-A")


@pytest.fixture
def author():
    return User.objects.create_user(username="pv-author")


class TestPromptVersion:
    def test_create_defaults(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            pv = PromptVersion.objects.create(
                tenant=tenant,
                skill_name="faq",
                body="You are a helpful assistant.",
                version=1,
                created_by=author,
            )
        pv.refresh_from_db()
        assert pv.is_active is False
        assert pv.traffic_percent == 100
        assert pv.published_at is None

    def test_unique_together_tenant_skill_version(self, tenant, author, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            PromptVersion.objects.create(
                tenant=tenant,
                skill_name="faq",
                body="v1",
                version=1,
                created_by=author,
            )
            with pytest.raises(IntegrityError):
                PromptVersion.objects.create(
                    tenant=tenant,
                    skill_name="faq",
                    body="duplicate",
                    version=1,
                    created_by=author,
                )

    def test_tenant_scoping(self, tenant, author, settings):
        other = Tenant.objects.create(slug="pv-b", name="PV-B")
        other_author = User.objects.create_user(username="pv-b-author")

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            PromptVersion.objects.create(
                tenant=tenant, skill_name="faq", body="A", version=1, created_by=author
            )
        with tenant_scope(other):
            PromptVersion.objects.create(
                tenant=other,
                skill_name="faq",
                body="B",
                version=1,
                created_by=other_author,
            )

        with tenant_scope(tenant):
            assert PromptVersion.objects.count() == 1
            assert PromptVersion.objects.first().body == "A"
        with tenant_scope(other):
            assert PromptVersion.objects.count() == 1
            assert PromptVersion.objects.first().body == "B"

        assert PromptVersion.all_tenants.count() == 2

    def test_str_marks_active(self, tenant, author):
        active = PromptVersion(
            tenant=tenant,
            skill_name="faq",
            body="x",
            version=1,
            created_by=author,
            is_active=True,
        )
        draft = PromptVersion(
            tenant=tenant,
            skill_name="faq",
            body="x",
            version=2,
            created_by=author,
            is_active=False,
        )
        assert "*" in str(active)
        assert "*" not in str(draft)
        assert "faq/v1" in str(active)

    def test_indexes_present(self):
        names = {tuple(idx.fields) for idx in PromptVersion._meta.indexes}
        assert ("tenant", "skill_name", "is_active") in names
