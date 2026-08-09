"""Tests for the ``cleanup_orphan_master_services`` command (DRF-967)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cleanup-test", name="Cleanup Test")


def _ts() -> datetime:
    return datetime(2026, 8, 9, 12, 0, tzinfo=dt_timezone.utc)


def _master(tenant: Tenant, *, external_id: int, name: str) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=_ts(),
        name=name,
    )


def _service(tenant: Tenant, *, external_id: int, name: str) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=_ts(),
        slug=f"svc-{external_id}",
        name=name,
    )


def _edge(
    tenant: Tenant,
    master: CatalogMaster,
    service: CatalogService,
    *,
    ayla_id: uuid.UUID | None = None,
) -> MasterService:
    return MasterService.all_tenants.create(
        tenant=tenant,
        master=master,
        service=service,
        ayla_specialist_service_id=ayla_id,
    )


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("cleanup_orphan_master_services", stdout=out, **kwargs)
    return out.getvalue()


@pytest.mark.django_db
def test_dry_run_reports_but_deletes_nothing(tenant: Tenant) -> None:
    master = _master(tenant, external_id=1, name="Анна")
    _edge(tenant, master, _service(tenant, external_id=1, name="LPG"))
    _edge(tenant, master, _service(tenant, external_id=2, name="Обёртывание"))

    output = _run(tenant_slug=tenant.slug)

    assert MasterService.all_tenants.filter(tenant=tenant).count() == 2
    assert "orphan     (NULL)       : 2" in output
    assert "DRY-RUN" in output


@pytest.mark.django_db
def test_apply_deletes_orphans_and_keeps_sync_owned(tenant: Tenant, tmp_path) -> None:
    master = _master(tenant, external_id=1, name="Анна")
    orphan_service = _service(tenant, external_id=1, name="LPG")
    real_service = _service(tenant, external_id=2, name="Прессотерапия")
    _edge(tenant, master, orphan_service)
    kept = _edge(tenant, master, real_service, ayla_id=uuid.uuid4())

    dump = tmp_path / "dump.json"
    _run(tenant_slug=tenant.slug, apply=True, dump=str(dump))

    remaining = list(MasterService.all_tenants.filter(tenant=tenant))
    assert [row.id for row in remaining] == [kept.id]

    payload = json.loads(dump.read_text(encoding="utf-8"))
    assert payload["row_count"] == 1
    assert payload["mode"] == "apply"
    assert payload["rows"][0]["service_name"] == "LPG"
    # The dump must carry enough to re-insert the row on rollback.
    assert payload["rows"][0]["master_id"] == str(master.id)
    assert payload["rows"][0]["ayla_specialist_service_id"] is None


@pytest.mark.django_db
def test_a_row_created_after_the_dump_is_not_deleted(tenant: Tenant, tmp_path, monkeypatch) -> None:
    """Everything deleted must be in the backup.

    The MM4 matrix creates NULL-provenance rows live from the admin UI. A
    delete driven by the bare predicate would take a row created between the
    dump and the delete — an operator's freshly-ticked cell, gone with no
    entry in the backup.
    """
    master = _master(tenant, external_id=1, name="Анна")
    _edge(tenant, master, _service(tenant, external_id=1, name="LPG"))
    late_service = _service(tenant, external_id=2, name="Добавлено оператором")

    from apps.catalog.management.commands import cleanup_orphan_master_services as mod

    original = mod.Command._write_dump

    def _write_then_race(self, path, **kwargs):
        original(self, path, **kwargs)
        _edge(tenant, master, late_service)

    monkeypatch.setattr(mod.Command, "_write_dump", _write_then_race)

    _run(tenant_slug=tenant.slug, apply=True, dump=str(tmp_path / "dump.json"))

    survivors = MasterService.all_tenants.filter(tenant=tenant)
    assert [row.service.name for row in survivors] == ["Добавлено оператором"]


@pytest.mark.django_db
def test_existing_dump_file_is_not_clobbered(tenant: Tenant, tmp_path) -> None:
    master = _master(tenant, external_id=1, name="Анна")
    _edge(tenant, master, _service(tenant, external_id=1, name="LPG"))
    dump = tmp_path / "dump.json"
    dump.write_text("previous backup", encoding="utf-8")

    with pytest.raises(CommandError, match="refusing to overwrite"):
        _run(tenant_slug=tenant.slug, apply=True, dump=str(dump))

    assert dump.read_text(encoding="utf-8") == "previous backup"
    assert MasterService.all_tenants.filter(tenant=tenant).count() == 1


@pytest.mark.django_db
def test_provenance_caveat_is_unconditional(tenant: Tenant) -> None:
    """No writer populates created_by, so the caveat must never be conditioned on it.

    A warning that only fires when created_by is set would never fire at all,
    and its silence would read as "no human MM4 edits among these rows" —
    exactly the false confidence that gets a destructive run approved.
    """
    master = _master(tenant, external_id=1, name="Анна")
    _edge(tenant, master, _service(tenant, external_id=1, name="LPG"))

    output = _run(tenant_slug=tenant.slug)

    assert "does NOT distinguish a human MM4 matrix edit" in output
    assert "AuditLog MASTER_SERVICES_CHANGED" in output


@pytest.mark.django_db
def test_stranded_masters_are_not_merged_by_name(tenant: Tenant) -> None:
    """Two masters can share a name; a namesake's kept edges must not hide one."""
    stranded = _master(tenant, external_id=1, name="Ольга")
    namesake = _master(tenant, external_id=2, name="Ольга")
    _edge(tenant, stranded, _service(tenant, external_id=1, name="LPG"))
    _edge(tenant, namesake, _service(tenant, external_id=2, name="Массаж"))
    _edge(
        tenant,
        namesake,
        _service(tenant, external_id=3, name="Прессотерапия"),
        ayla_id=uuid.uuid4(),
    )

    output = _run(tenant_slug=tenant.slug)

    assert "1 master(s) will have ZERO edges" in output


@pytest.mark.django_db
def test_apply_requires_a_dump_path(tenant: Tenant) -> None:
    master = _master(tenant, external_id=1, name="Анна")
    _edge(tenant, master, _service(tenant, external_id=1, name="LPG"))

    with pytest.raises(CommandError, match="--apply requires --dump"):
        _run(tenant_slug=tenant.slug, apply=True)

    assert MasterService.all_tenants.filter(tenant=tenant).count() == 1


@pytest.mark.django_db
def test_other_tenants_are_untouched(tenant: Tenant, tmp_path) -> None:
    other = Tenant.objects.create(slug="cleanup-other", name="Other")
    other_master = _master(other, external_id=9, name="Чужой мастер")
    other_edge = _edge(other, other_master, _service(other, external_id=9, name="Чужая услуга"))

    master = _master(tenant, external_id=1, name="Анна")
    _edge(tenant, master, _service(tenant, external_id=1, name="LPG"))

    _run(tenant_slug=tenant.slug, apply=True, dump=str(tmp_path / "d.json"))

    assert MasterService.all_tenants.filter(id=other_edge.id).exists()
    assert not MasterService.all_tenants.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_warns_about_masters_left_with_zero_edges(tenant: Tenant) -> None:
    stranded = _master(tenant, external_id=1, name="Останется без услуг")
    covered = _master(tenant, external_id=2, name="Останется с услугой")
    _edge(tenant, stranded, _service(tenant, external_id=1, name="LPG"))
    _edge(tenant, covered, _service(tenant, external_id=2, name="Массаж"))
    _edge(
        tenant,
        covered,
        _service(tenant, external_id=3, name="Прессотерапия"),
        ayla_id=uuid.uuid4(),
    )

    output = _run(tenant_slug=tenant.slug)

    assert "ZERO edges" in output
    assert "Останется без услуг" in output
    assert "Останется с услугой" not in output.split("ZERO edges")[1]


@pytest.mark.django_db
def test_no_orphans_is_a_clean_no_op(tenant: Tenant) -> None:
    master = _master(tenant, external_id=1, name="Анна")
    _edge(tenant, master, _service(tenant, external_id=1, name="LPG"), ayla_id=uuid.uuid4())

    output = _run(tenant_slug=tenant.slug)

    assert "nothing to clean" in output
    assert MasterService.all_tenants.filter(tenant=tenant).count() == 1


@pytest.mark.django_db
def test_tenant_selector_is_exclusive(tenant: Tenant) -> None:
    with pytest.raises(CommandError, match="exactly one"):
        _run()
    with pytest.raises(CommandError, match="exactly one"):
        _run(tenant_slug=tenant.slug, tenant_id=str(tenant.id))


@pytest.mark.django_db
def test_tenant_can_be_selected_by_id(tenant: Tenant) -> None:
    master = _master(tenant, external_id=1, name="Анна")
    _edge(tenant, master, _service(tenant, external_id=1, name="LPG"))

    output = _run(tenant_id=str(tenant.id))

    assert "orphan     (NULL)       : 1" in output


@pytest.mark.django_db
def test_unknown_tenant_is_an_error(db) -> None:
    with pytest.raises(CommandError, match="tenant not found"):
        _run(tenant_slug="does-not-exist")
