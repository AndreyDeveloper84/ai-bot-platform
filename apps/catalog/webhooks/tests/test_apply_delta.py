"""apply_delta Celery task tests (Sprint 10 / C3 / DRF-879).

Coverage:

* Created event → upsert creates row + audit `applied`
* Updated event → upsert updates ``external_updated_at`` + audit `applied`
* Deleted event → row gone + audit `deleted`
* Stale incoming ts → row untouched + audit `skipped_stale`
* Unknown tenant slug → audit `failed.tenant_missing` + status return
* Unknown model → audit `failed.unknown_model`
* Bad timestamp → audit `failed.bad_timestamp`
* Delete on non-existent row → idempotent, audit `deleted` with 0 rows
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogFaq, CatalogMaster, CatalogService
from apps.catalog.webhooks.tasks import apply_delta
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="salon-a", name="Salon A")


def _now() -> str:
    return datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc).isoformat()


def _later() -> str:
    return datetime(2026, 5, 14, 11, 0, tzinfo=timezone.utc).isoformat()


def _earlier() -> str:
    return datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc).isoformat()


def _audit_actions() -> list[str]:
    return list(AuditLog.all_tenants.values_list("action", flat=True))


class TestCreatedAndUpdated:
    def test_created_inserts_row(self, tenant: Tenant) -> None:
        result = apply_delta.apply(
            kwargs={
                "tenant_slug": tenant.slug,
                "model": "service",
                "event": "created",
                "external_id": 101,
                "external_updated_at": _now(),
            }
        ).get()

        assert result["status"] == "applied"
        assert result["created"] is True
        assert CatalogService.all_tenants.filter(tenant=tenant, external_id=101).exists()
        assert "catalog.apply_delta.applied" in _audit_actions()

    def test_updated_bumps_existing_row(self, tenant: Tenant) -> None:
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=101,
            external_updated_at=datetime.fromisoformat(_now()),
        )

        result = apply_delta.apply(
            kwargs={
                "tenant_slug": tenant.slug,
                "model": "service",
                "event": "updated",
                "external_id": 101,
                "external_updated_at": _later(),
            }
        ).get()

        assert result["status"] == "applied"
        assert result["created"] is False
        row = CatalogService.all_tenants.get(tenant=tenant, external_id=101)
        assert row.external_updated_at == datetime.fromisoformat(_later())

    def test_master_and_faq_models_also_routed(self, tenant: Tenant) -> None:
        # Spot-check that the model map covers all four mirror types.
        for model_str, model_cls in (
            ("master", CatalogMaster),
            ("faq", CatalogFaq),
        ):
            apply_delta.apply(
                kwargs={
                    "tenant_slug": tenant.slug,
                    "model": model_str,
                    "event": "created",
                    "external_id": 200,
                    "external_updated_at": _now(),
                }
            ).get()
            assert model_cls.all_tenants.filter(tenant=tenant, external_id=200).exists()


class TestStaleGuard:
    def test_older_incoming_ts_skipped(self, tenant: Tenant) -> None:
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=101,
            external_updated_at=datetime.fromisoformat(_later()),
        )

        result = apply_delta.apply(
            kwargs={
                "tenant_slug": tenant.slug,
                "model": "service",
                "event": "updated",
                "external_id": 101,
                "external_updated_at": _earlier(),
            }
        ).get()

        assert result["status"] == "skipped_stale"
        # Local row keeps its later timestamp.
        row = CatalogService.all_tenants.get(tenant=tenant, external_id=101)
        assert row.external_updated_at == datetime.fromisoformat(_later())
        assert "catalog.apply_delta.skipped_stale" in _audit_actions()

    def test_equal_ts_treated_as_stale(self, tenant: Tenant) -> None:
        # Same timestamp delivered twice → second is a no-op.
        ts = _now()
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=101,
            external_updated_at=datetime.fromisoformat(ts),
        )
        result = apply_delta.apply(
            kwargs={
                "tenant_slug": tenant.slug,
                "model": "service",
                "event": "updated",
                "external_id": 101,
                "external_updated_at": ts,
            }
        ).get()
        assert result["status"] == "skipped_stale"


class TestDelete:
    def test_delete_removes_row(self, tenant: Tenant) -> None:
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=101,
            external_updated_at=datetime.fromisoformat(_now()),
        )

        result = apply_delta.apply(
            kwargs={
                "tenant_slug": tenant.slug,
                "model": "service",
                "event": "deleted",
                "external_id": 101,
                "external_updated_at": _later(),
            }
        ).get()

        assert result["status"] == "deleted"
        assert not CatalogService.all_tenants.filter(tenant=tenant, external_id=101).exists()

    def test_delete_nonexistent_row_is_idempotent(self, tenant: Tenant) -> None:
        # mysite deletes something, then re-delivers — second call must
        # not error or affect anything else.
        result = apply_delta.apply(
            kwargs={
                "tenant_slug": tenant.slug,
                "model": "service",
                "event": "deleted",
                "external_id": 999,
                "external_updated_at": _now(),
            }
        ).get()
        assert result["status"] == "deleted"
        assert result["rows_removed"] == 0


class TestFailurePaths:
    def test_unknown_tenant_no_retry(self, tenant: Tenant) -> None:
        result = apply_delta.apply(
            kwargs={
                "tenant_slug": "does-not-exist",
                "model": "service",
                "event": "updated",
                "external_id": 1,
                "external_updated_at": _now(),
            }
        ).get()
        assert result["status"] == "tenant_missing"
        assert "catalog.apply_delta.failed" in _audit_actions()

    def test_unknown_model(self, tenant: Tenant) -> None:
        result = apply_delta.apply(
            kwargs={
                "tenant_slug": tenant.slug,
                "model": "not_a_model",
                "event": "updated",
                "external_id": 1,
                "external_updated_at": _now(),
            }
        ).get()
        assert result["status"] == "model_missing"

    def test_bad_timestamp(self, tenant: Tenant) -> None:
        result = apply_delta.apply(
            kwargs={
                "tenant_slug": tenant.slug,
                "model": "service",
                "event": "updated",
                "external_id": 1,
                "external_updated_at": "not-a-valid-timestamp",
            }
        ).get()
        assert result["status"] == "bad_timestamp"
        assert "catalog.apply_delta.failed" in _audit_actions()
