"""Catalog→KB projector tests (DRF-565 / Sprint 7 / K7)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.catalog.models import (
    CatalogFaq,
    CatalogHelpArticle,
    CatalogMaster,
    CatalogService,
)
from apps.kb.models import KbDocType, KbDocument
from apps.kb.projectors import (
    ProjectionResult,
    faq_to_body,
    help_article_to_body,
    master_to_body,
    project_tenant_catalog,
    service_to_body,
)
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="kb-proj", name="KB Proj")


def _ts() -> datetime:
    return datetime(2026, 5, 13, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Body builders — pure functions
# ---------------------------------------------------------------------------


class TestServiceToBody:
    def test_full_service_body_includes_all_fields(self, tenant: Tenant) -> None:
        svc = CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1,
            external_updated_at=_ts(),
            slug="massazh",
            name="Массаж спины",
            short_description="Релакс",
            description="Долгое описание",
            price_from=Decimal("1500"),
            duration_min=60,
            goals=["relax", "back_pain"],
            requires_health_check=True,
            contraindications="Беременность",
        )
        body = service_to_body(svc)
        assert "Массаж спины" in body
        assert "Релакс" in body
        assert "1500" in body
        assert "60 мин" in body
        assert "relax" in body
        assert "медконсульт" in body.lower()
        assert "Беременность" in body

    def test_optional_fields_skipped(self, tenant: Tenant) -> None:
        svc = CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=2,
            external_updated_at=_ts(),
            slug="s",
            name="Service",
        )
        body = service_to_body(svc)
        assert body.startswith("Услуга: Service")
        # No "Цена" line for missing price.
        assert "Цена" not in body


class TestMasterToBody:
    def test_master_body(self, tenant: Tenant) -> None:
        m = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=10,
            external_updated_at=_ts(),
            name="Анна",
            specialization="Массажист",
            bio="Опыт 5 лет",
            rating=Decimal("4.80"),
        )
        body = master_to_body(m)
        assert "Мастер: Анна" in body
        assert "Массажист" in body
        assert "4.8" in body


class TestFaqAndHelpBody:
    def test_faq_qa_format(self, tenant: Tenant) -> None:
        f = CatalogFaq.all_tenants.create(
            tenant=tenant,
            external_id=20,
            external_updated_at=_ts(),
            question="Часы работы?",
            answer="Пн-Вс 10-22",
        )
        body = faq_to_body(f)
        assert body == "Q: Часы работы?\nA: Пн-Вс 10-22"

    def test_help_article_concat(self, tenant: Tenant) -> None:
        h = CatalogHelpArticle.all_tenants.create(
            tenant=tenant,
            external_id=30,
            external_updated_at=_ts(),
            question="Как записаться?",
            answer="Через сайт.",
        )
        body = help_article_to_body(h)
        assert "Как записаться?" in body
        assert "Через сайт" in body


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _seed(tenant: Tenant) -> None:
    CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=_ts(),
        slug="s",
        name="Service A",
    )
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=10,
        external_updated_at=_ts(),
        name="Master A",
    )
    CatalogFaq.all_tenants.create(
        tenant=tenant,
        external_id=20,
        external_updated_at=_ts(),
        question="Q?",
        answer="A",
    )
    CatalogHelpArticle.all_tenants.create(
        tenant=tenant,
        external_id=30,
        external_updated_at=_ts(),
        question="HQ",
        answer="HA",
    )


class TestProjectionOrchestrator:
    def test_first_pass_creates_one_per_mirror(self, tenant: Tenant) -> None:
        _seed(tenant)
        results = project_tenant_catalog(tenant)
        assert results[KbDocType.SERVICE].created == 1
        assert results[KbDocType.MASTER].created == 1
        assert results[KbDocType.FAQ].created == 1
        assert results[KbDocType.HELP_ARTICLE].created == 1
        # 4 KbDocument rows landed.
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 4

    def test_second_pass_no_changes_marks_unchanged(self, tenant: Tenant) -> None:
        _seed(tenant)
        project_tenant_catalog(tenant)
        results = project_tenant_catalog(tenant)
        assert results[KbDocType.SERVICE].unchanged == 1
        assert results[KbDocType.SERVICE].updated == 0
        assert results[KbDocType.SERVICE].created == 0

    def test_content_edit_marks_updated(self, tenant: Tenant) -> None:
        _seed(tenant)
        project_tenant_catalog(tenant)

        svc = CatalogService.all_tenants.get(tenant=tenant, external_id=1)
        svc.description = "Edited"
        svc.save()

        results = project_tenant_catalog(tenant)
        assert results[KbDocType.SERVICE].updated == 1
        assert results[KbDocType.SERVICE].unchanged == 0

        # KbDocument body now reflects the edit.
        kb = KbDocument.all_tenants.get(tenant=tenant, source_uri="mysite://services/1", version=1)
        assert "Edited" in kb.content

    def test_source_uri_per_doc_type(self, tenant: Tenant) -> None:
        _seed(tenant)
        project_tenant_catalog(tenant)
        uris = set(
            KbDocument.all_tenants.filter(tenant=tenant).values_list("source_uri", flat=True)
        )
        assert uris == {
            "mysite://services/1",
            "mysite://masters/10",
            "mysite://faqs/20",
            "mysite://help-articles/30",
        }

    def test_metadata_records_origin_model(self, tenant: Tenant) -> None:
        _seed(tenant)
        project_tenant_catalog(tenant)
        kb = KbDocument.all_tenants.get(tenant=tenant, source_uri="mysite://services/1")
        assert kb.metadata["projected_from"] == "CatalogService"


class TestProjectionResultDataclass:
    def test_defaults_zero(self) -> None:
        r = ProjectionResult()
        assert (r.created, r.updated, r.unchanged) == (0, 0, 0)
