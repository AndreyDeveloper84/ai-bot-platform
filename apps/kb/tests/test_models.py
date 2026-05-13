"""KbDocument model tests (DRF-558 / Sprint 7 / K1)."""

from __future__ import annotations

import hashlib

import pytest
from django.db.utils import IntegrityError

from apps.kb.models import KbDocType, KbDocument
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="kb-test", name="KB Test")


class TestKbDocumentBasics:
    def test_create_minimal(self, tenant: Tenant) -> None:
        doc = KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/1",
            content="Часы работы: 10-22",
        )
        assert doc.version == 1
        assert doc.locale == "ru-RU"
        assert doc.embedded_at is None  # initial state — needs embedding

    def test_str_representation(self, tenant: Tenant) -> None:
        doc = KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.SERVICE,
            source_uri="mysite://services/42",
            version=3,
            content="Массаж спины 60 минут",
        )
        assert str(doc) == "KbDocument[service:mysite://services/42@v3]"

    def test_doc_type_choices_locked(self) -> None:
        # Sprint 7 / K1 locks exactly six values. Tests guard against
        # accidental drift — adding a new value requires a migration
        # AND updating the chunker strategy table in K3 (DRF-561).
        assert set(KbDocType.values) == {
            "service",
            "master",
            "contraindication",
            "faq",
            "help_article",
            "legal",
        }


class TestChecksum:
    def test_auto_computed_on_save(self, tenant: Tenant) -> None:
        content = "Стоимость лазерной эпиляции — от 1500₽"
        doc = KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/laser-price",
            content=content,
        )
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert doc.checksum == expected

    def test_preserved_when_caller_sets_it(self, tenant: Tenant) -> None:
        # Tests that exercise the K4 "checksum mismatch → re-embed" path
        # need to construct a row with a *wrong* checksum on purpose.
        doc = KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/forced",
            content="content X",
            checksum="deadbeef" * 8,
        )
        assert doc.checksum == "deadbeef" * 8

    def test_compute_checksum_unicode_stable(self) -> None:
        a = KbDocument.compute_checksum("часы работы")
        b = KbDocument.compute_checksum("часы работы")
        assert a == b
        assert len(a) == 64  # hex SHA-256


class TestUniqueness:
    def test_same_source_different_version_allowed(self, tenant: Tenant) -> None:
        KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.SERVICE,
            source_uri="mysite://services/1",
            version=1,
            content="v1 text",
        )
        # Bumping version is normal upstream-change flow.
        KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.SERVICE,
            source_uri="mysite://services/1",
            version=2,
            content="v2 text",
        )
        assert KbDocument.all_tenants.filter(source_uri="mysite://services/1").count() == 2

    def test_duplicate_version_rejected(self, tenant: Tenant) -> None:
        KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.SERVICE,
            source_uri="mysite://services/2",
            version=1,
            content="x",
        )
        with pytest.raises(IntegrityError):
            KbDocument.all_tenants.create(
                tenant=tenant,
                doc_type=KbDocType.SERVICE,
                source_uri="mysite://services/2",
                version=1,
                content="y",
            )

    def test_same_uri_different_tenants_allowed(self, db) -> None:
        a = Tenant.objects.create(slug="kb-tenant-a", name="A")
        b = Tenant.objects.create(slug="kb-tenant-b", name="B")
        KbDocument.all_tenants.create(
            tenant=a,
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/1",
            content="A's copy",
        )
        # Different tenant — same external row is intentionally distinct.
        KbDocument.all_tenants.create(
            tenant=b,
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/1",
            content="B's copy",
        )
        assert KbDocument.all_tenants.filter(source_uri="mysite://faq/1").count() == 2


class TestTenantScoping:
    def test_default_manager_scopes_to_current_tenant(self, db) -> None:
        from apps.tenancy.context import tenant_scope

        a = Tenant.objects.create(slug="kb-scope-a", name="A")
        b = Tenant.objects.create(slug="kb-scope-b", name="B")
        KbDocument.all_tenants.create(tenant=a, doc_type=KbDocType.FAQ, source_uri="a", content="x")
        KbDocument.all_tenants.create(tenant=b, doc_type=KbDocType.FAQ, source_uri="b", content="y")

        with tenant_scope(a):
            visible = list(KbDocument.objects.all())
        assert len(visible) == 1
        assert visible[0].tenant_id == a.id

    def test_all_tenants_manager_sees_everything(self, db) -> None:
        a = Tenant.objects.create(slug="kb-all-a", name="A")
        b = Tenant.objects.create(slug="kb-all-b", name="B")
        KbDocument.all_tenants.create(tenant=a, doc_type=KbDocType.FAQ, source_uri="a", content="x")
        KbDocument.all_tenants.create(tenant=b, doc_type=KbDocType.FAQ, source_uri="b", content="y")
        assert KbDocument.all_tenants.count() == 2
