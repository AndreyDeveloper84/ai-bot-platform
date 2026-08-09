"""Tests for the ``seed_dev_formula_tela`` dev fixture command (DRF-967)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.tenancy.models import Tenant


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("seed_dev_formula_tela", stdout=out, **kwargs)
    return out.getvalue()


def _ts() -> datetime:
    return datetime(2026, 8, 9, 12, 0, tzinfo=dt_timezone.utc)


@pytest.mark.django_db
def test_seeded_edges_are_owned_by_sync() -> None:
    """Every seeded edge carries provenance, so sync can reconcile it away.

    An unstamped edge is invisible to reconciliation forever — the DRF-967
    failure mode.
    """
    _run()

    tenant = Tenant.objects.get(slug="formula-tela")
    edges = list(MasterService.all_tenants.filter(tenant=tenant))
    assert edges, "seed must create master↔service edges"
    assert all(row.ayla_specialist_service_id is not None for row in edges)


@pytest.mark.django_db
def test_seed_does_not_create_a_cartesian_product() -> None:
    """Each master gets its declared services — not the whole catalog."""
    _run()

    tenant = Tenant.objects.get(slug="formula-tela")
    services = CatalogService.all_tenants.filter(tenant=tenant).count()
    per_master = {
        master.name: MasterService.all_tenants.filter(tenant=tenant, master=master).count()
        for master in CatalogMaster.all_tenants.filter(tenant=tenant)
    }
    assert per_master == {"Анна Иванова": 3, "Мария Петрова": 2}
    assert sum(per_master.values()) < services * len(per_master)


@pytest.mark.django_db
def test_rerun_is_idempotent_and_stamps_are_stable() -> None:
    _run()
    tenant = Tenant.objects.get(slug="formula-tela")
    before = {
        (row.master_id, row.service_id): row.ayla_specialist_service_id
        for row in MasterService.all_tenants.filter(tenant=tenant)
    }

    _run()

    after = {
        (row.master_id, row.service_id): row.ayla_specialist_service_id
        for row in MasterService.all_tenants.filter(tenant=tenant)
    }
    assert after == before


@pytest.mark.django_db
def test_rerun_adopts_legacy_unstamped_rows() -> None:
    """Rows left NULL by an older seed run become reconcilable again."""
    _run()
    tenant = Tenant.objects.get(slug="formula-tela")
    MasterService.all_tenants.filter(tenant=tenant).update(ayla_specialist_service_id=None)

    _run()

    assert not MasterService.all_tenants.filter(
        tenant=tenant, ayla_specialist_service_id__isnull=True
    ).exists()


@pytest.mark.django_db
def test_refuses_to_seed_over_a_live_ayla_mirror() -> None:
    tenant = Tenant.objects.create(slug="formula-tela", name="Формула тела")
    CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=5001,
        external_updated_at=_ts(),
        slug="real-service",
        name="Настоящая услуга",
        ayla_service_id=uuid.uuid4(),
    )

    with pytest.raises(CommandError, match="already mirrors Ayla"):
        _run()

    # Nothing from the fixture landed.
    assert not CatalogService.all_tenants.filter(tenant=tenant, external_id=1001).exists()


@pytest.mark.django_db
def test_force_overrides_the_mirror_guard() -> None:
    tenant = Tenant.objects.create(slug="formula-tela", name="Формула тела")
    CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=5001,
        external_updated_at=_ts(),
        slug="real-service",
        name="Настоящая услуга",
        ayla_service_id=uuid.uuid4(),
    )

    _run(force=True)

    assert CatalogService.all_tenants.filter(tenant=tenant, external_id=1001).exists()
