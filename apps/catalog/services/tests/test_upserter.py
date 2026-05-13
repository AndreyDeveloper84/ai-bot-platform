"""Catalog upserter tests (DRF-574 / Sprint 7 / C3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.catalog.models import (
    CatalogFaq,
    CatalogMaster,
    CatalogService,
)
from apps.catalog.services.http_client import (
    CatalogFaqDTO,
    CatalogMasterDTO,
    CatalogServiceDTO,
)
from apps.catalog.services.upserter import (
    UpsertResult,
    upsert_faqs,
    upsert_masters,
    upsert_services,
)
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cat-up", name="Cat Up")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="cat-up-b", name="Cat Up B")


def _svc(external_id: int, *, name: str = "S", ts: datetime | None = None) -> CatalogServiceDTO:
    return CatalogServiceDTO(
        external_id=external_id,
        external_updated_at=ts or datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
        slug=f"slug-{external_id}",
        name=name,
    )


class TestCreateAndUpdate:
    def test_first_run_inserts(self, tenant: Tenant) -> None:
        result = upsert_services(tenant, [_svc(1), _svc(2)])
        assert isinstance(result, UpsertResult)
        assert result.created == 2
        assert result.updated == 0
        assert result.skipped == 0
        assert CatalogService.all_tenants.filter(tenant=tenant).count() == 2

    def test_re_run_with_newer_ts_updates(self, tenant: Tenant) -> None:
        upsert_services(tenant, [_svc(1, name="Old")])
        result = upsert_services(
            tenant,
            [
                _svc(
                    1,
                    name="New",
                    ts=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
                )
            ],
        )
        assert result.updated == 1
        assert result.created == 0
        row = CatalogService.all_tenants.get(tenant=tenant, external_id=1)
        assert row.name == "New"


class TestLastWriterWins:
    """Decision 10 — upstream `external_updated_at` decides, NOT wall clock."""

    def test_stale_dto_skipped(self, tenant: Tenant) -> None:
        fresh = datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)
        stale = fresh - timedelta(hours=1)
        upsert_services(tenant, [_svc(1, name="Fresh", ts=fresh)])

        # Race: a second beat with an older timestamp arrives.
        result = upsert_services(tenant, [_svc(1, name="Stale", ts=stale)])
        assert result.skipped == 1
        assert result.updated == 0
        row = CatalogService.all_tenants.get(tenant=tenant, external_id=1)
        # Stale name didn't overwrite.
        assert row.name == "Fresh"

    def test_equal_ts_treated_as_skip(self, tenant: Tenant) -> None:
        same = datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)
        upsert_services(tenant, [_svc(1, ts=same)])
        result = upsert_services(tenant, [_svc(1, ts=same)])
        assert result.skipped == 1
        assert result.updated == 0


class TestPerRowErrorIsolation:
    """One malformed DTO must not poison the rest of the batch."""

    def test_bad_row_logged_others_committed(
        self, tenant: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good = _svc(1, name="Good")
        # Patch the field mapper to raise on a specific external_id.
        from apps.catalog.services import upserter

        original = upserter._service_fields

        def boom(dto: CatalogServiceDTO) -> dict:
            if dto.external_id == 2:
                raise ValueError("synthetic")
            return original(dto)

        monkeypatch.setattr(upserter, "_service_fields", boom)

        result = upsert_services(
            tenant,
            [good, _svc(2, name="Bad"), _svc(3, name="Good3")],
        )
        # The boom row didn't land.
        assert result.created == 2
        assert len(result.errors) == 1
        assert result.errors[0]["external_id"] == 2
        assert "synthetic" in result.errors[0]["reason"]
        # Other two committed.
        ids = sorted(
            CatalogService.all_tenants.filter(tenant=tenant).values_list("external_id", flat=True)
        )
        assert ids == [1, 3]


class TestCrossTenant:
    def test_same_external_id_two_tenants(self, tenant: Tenant, tenant_b: Tenant) -> None:
        upsert_services(tenant, [_svc(1, name="A")])
        upsert_services(tenant_b, [_svc(1, name="B")])
        assert CatalogService.all_tenants.filter(external_id=1).count() == 2


class TestAllMirrors:
    def test_master_upsert(self, tenant: Tenant) -> None:
        dto = CatalogMasterDTO(
            external_id=10,
            external_updated_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            name="Анна",
            specialization="Массаж",
        )
        result = upsert_masters(tenant, [dto])
        assert result.created == 1
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

    def test_faq_upsert(self, tenant: Tenant) -> None:
        dto = CatalogFaqDTO(
            external_id=99,
            external_updated_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            question="Q",
            answer="A",
        )
        result = upsert_faqs(tenant, [dto])
        assert result.created == 1
        assert CatalogFaq.all_tenants.filter(tenant=tenant).count() == 1


class TestEmptyBatch:
    def test_no_rows_no_op(self, tenant: Tenant) -> None:
        result = upsert_services(tenant, [])
        assert result.created == 0
        assert result.updated == 0
        assert result.skipped == 0
        assert result.errors == []
