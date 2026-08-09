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


@pytest.fixture(autouse=True)
def _dev_environment(settings):
    """The command refuses to run outside DEBUG; these tests exercise the dev path."""
    settings.DEBUG = True


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
def test_refuses_to_run_outside_debug(settings) -> None:
    settings.DEBUG = False

    with pytest.raises(CommandError, match="DEBUG is False"):
        _run()

    assert not Tenant.objects.filter(slug="formula-tela").exists()


@pytest.mark.django_db
def test_seeded_edges_are_reconciled_by_a_real_sync_beat() -> None:
    """The whole justification for stamping: sync OWNS these rows afterwards.

    Writing a synthetic id into a field documented as Ayla's canonical id is
    only defensible if it really hands the row to sync — so drive the actual
    upserter, not a description of it. One seeded pair is published upstream and
    must end up carrying Ayla's real edge id; the rest are absent upstream and
    must be reconciled away.
    """
    from apps.catalog.services.http_client import CatalogSpecialistServiceDTO
    from apps.catalog.services.upserter import upsert_master_services

    _run()
    tenant = Tenant.objects.get(slug="formula-tela")
    survivor = MasterService.all_tenants.filter(tenant=tenant)[0]
    # The upserter resolves an edge's service by ayla_service_id, so the
    # published pair needs a grounded service — exactly as on a synced tenant.
    real_service_id = uuid.uuid4()
    CatalogService.all_tenants.filter(id=survivor.service_id).update(
        ayla_service_id=real_service_id
    )
    real_edge_id = uuid.uuid4()

    dto = CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=str(real_edge_id),
        salon_service=str(real_service_id),
        specialist=str(survivor.master_id),
        external_updated_at=_ts(),
        tenant=str(tenant.id),
    )

    result = upsert_master_services(tenant, [dto])

    survivor.refresh_from_db()
    assert survivor.ayla_specialist_service_id == real_edge_id
    # Every other seeded edge was absent from the snapshot → reconciled away.
    assert MasterService.all_tenants.filter(tenant=tenant).count() == 1
    assert result.removed >= 1


@pytest.mark.django_db
def test_force_never_overwrites_a_real_ayla_stamp() -> None:
    """A --force run over a live mirror must not replace canonical provenance."""
    _run()
    tenant = Tenant.objects.get(slug="formula-tela")
    edge = MasterService.all_tenants.filter(tenant=tenant)[0]
    real_ayla_id = uuid.uuid4()
    MasterService.all_tenants.filter(id=edge.id).update(ayla_specialist_service_id=real_ayla_id)

    _run(force=True)

    edge.refresh_from_db()
    assert edge.ayla_specialist_service_id == real_ayla_id


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
