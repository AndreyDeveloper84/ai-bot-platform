"""``link_ayla_service_ids`` tests — S1-B pilot catalog linking.

Covers the matching engine (:mod:`apps.catalog.services.linking`) and the
management command wrapper. The command back-fills
``CatalogService.ayla_service_id`` for legacy rows (NULL link) by matching
them against Ayla salon-services (slug → normalized name) so the
``BOOKING_VIA_AYLA_REST`` flip grounds on ~100% coverage for Penza pilot
services.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import CatalogService
from apps.catalog.services.http_client import CatalogSalonServiceDTO
from apps.catalog.services.linking import link_tenant_services
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="penza-pilot", name="Penza Pilot")


def _dto(
    ayla_service_id: str | None = None,
    *,
    name: str = "Маникюр",
    raw: dict | None = None,
) -> CatalogSalonServiceDTO:
    aid = ayla_service_id or str(uuid.uuid4())
    return CatalogSalonServiceDTO(
        ayla_service_id=aid,
        external_updated_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        name=name,
        template="9d3f0000-0000-4000-8000-000000000002",
        raw=raw if raw is not None else {"id": aid},
    )


def _legacy_row(
    tenant: Tenant,
    *,
    name: str,
    slug: str,
    is_active: bool = True,
) -> CatalogService:
    """A pre-S3B catalog row: legacy integer external_id, NULL Ayla link."""
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=abs(hash(slug)) % 10_000_000,
        external_updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        name=name,
        slug=slug,
        is_active=is_active,
    )


class TestSlugMatching:
    def test_exact_slug_match_links(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Маникюр", slug="manikyur")
        aid = str(uuid.uuid4())
        dto = _dto(aid, name="Маникюр классический", raw={"id": aid, "template_slug": "manikyur"})

        report = link_tenant_services(tenant, [dto], apply=True)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid
        assert len(report.links) == 1
        assert report.links[0].matched_by == "slug"

    def test_slug_from_nested_template_dict(self, tenant: Tenant) -> None:
        """Forward-compat: W1 may expose template as an object with slug."""
        row = _legacy_row(tenant, name="Педикюр", slug="pedikyur")
        aid = str(uuid.uuid4())
        dto = _dto(aid, raw={"id": aid, "template": {"id": "x", "slug": "pedikyur"}})

        report = link_tenant_services(tenant, [dto], apply=True)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid
        assert report.links[0].matched_by == "slug"

    def test_ambiguous_slug_not_linked(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Маникюр", slug="manikyur")
        dto1 = _dto(name="Маникюр А", raw={"template_slug": "manikyur"})
        dto2 = _dto(name="Маникюр Б", raw={"template_slug": "manikyur"})

        report = link_tenant_services(tenant, [dto1, dto2], apply=True)

        row.refresh_from_db()
        assert row.ayla_service_id is None
        assert len(report.unmatched) == 1


class TestNameMatching:
    def test_normalized_name_match(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="  маникюр   аппаратный ", slug="man-app")
        aid = str(uuid.uuid4())
        dto = _dto(aid, name="Маникюр аппаратный")

        report = link_tenant_services(tenant, [dto], apply=True)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid
        assert report.links[0].matched_by == "name"

    def test_yo_normalization(self, tenant: Tenant) -> None:
        """Russian ё/е must not break matching («Массаж лица» vs «Массаж ліца»)."""
        row = _legacy_row(tenant, name="Моделирование вёдер", slug="model")
        aid = str(uuid.uuid4())
        dto = _dto(aid, name="Моделирование ведер")

        link_tenant_services(tenant, [dto], apply=True)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid

    def test_ambiguous_name_not_linked(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Маникюр", slug="manikyur")
        dto1 = _dto(name="Маникюр")
        dto2 = _dto(name="  Маникюр  ")

        report = link_tenant_services(tenant, [dto1, dto2], apply=True)

        row.refresh_from_db()
        assert row.ayla_service_id is None
        assert len(report.unmatched) == 1

    def test_no_match_reported_unmatched(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Солярий", slug="solyariy")

        report = link_tenant_services(tenant, [_dto(name="Маникюр")], apply=True)

        row.refresh_from_db()
        assert row.ayla_service_id is None
        assert [u.slug for u in report.unmatched] == ["solyariy"]


class TestDryRun:
    def test_dry_run_reports_without_writing(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Маникюр", slug="manikyur")
        dto = _dto(name="Маникюр")

        report = link_tenant_services(tenant, [dto], apply=False)

        row.refresh_from_db()
        assert row.ayla_service_id is None
        assert len(report.links) == 1  # projected, not applied


class TestDuplicates:
    def test_existing_ayla_row_blocks_stamp(self, tenant: Tenant) -> None:
        legacy = _legacy_row(tenant, name="Маникюр", slug="manikyur")
        aid = str(uuid.uuid4())
        # Ayla-fed row already carries the link (created by S3B sync).
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            name="Маникюр",
            slug="",
            ayla_service_id=aid,
        )

        report = link_tenant_services(tenant, [_dto(aid)], apply=True)

        legacy.refresh_from_db()
        assert legacy.ayla_service_id is None
        assert legacy.is_active is True  # untouched without the flag
        assert len(report.duplicates) == 1
        assert report.duplicates[0].ayla_service_id == aid
        assert report.duplicates[0].deactivated is False

    def test_deactivate_flag_retires_legacy_duplicate(self, tenant: Tenant) -> None:
        legacy = _legacy_row(tenant, name="Маникюр", slug="manikyur")
        aid = str(uuid.uuid4())
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            name="Маникюр",
            slug="",
            ayla_service_id=aid,
        )

        report = link_tenant_services(tenant, [_dto(aid)], apply=True, deactivate_duplicates=True)

        legacy.refresh_from_db()
        assert legacy.ayla_service_id is None
        assert legacy.is_active is False
        assert report.duplicates[0].deactivated is True


class TestScopeRules:
    def test_already_linked_rows_untouched(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            name="Маникюр",
            slug="manikyur",
            ayla_service_id=aid,
        )
        other = str(uuid.uuid4())

        report = link_tenant_services(tenant, [_dto(other)], apply=True)

        assert str(CatalogService.all_tenants.get(slug="manikyur").ayla_service_id) == aid
        assert report.links == []
        assert report.unmatched == []

    def test_inactive_rows_ignored(self, tenant: Tenant) -> None:
        _legacy_row(tenant, name="Маникюр", slug="manikyur", is_active=False)

        report = link_tenant_services(tenant, [_dto()], apply=True)

        assert report.active_before == 0
        assert report.links == []
        assert report.unmatched == []


class TestCoverage:
    def test_coverage_projection(self, tenant: Tenant) -> None:
        _legacy_row(tenant, name="Маникюр", slug="manikyur")
        _legacy_row(tenant, name="Солярий", slug="solyariy")
        dto = _dto(name="Маникюр")

        report = link_tenant_services(tenant, [dto], apply=False)

        assert report.active_before == 2
        assert report.covered_before == 0
        assert report.coverage_after == pytest.approx(0.5)


class TestCommand:
    def _patch_client(self, monkeypatch: pytest.MonkeyPatch, dtos) -> None:
        class _FakeClient:
            def __init__(self, **kwargs) -> None: ...
            def fetch_salon_services(self, *, tenant_id: str):
                return dtos

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None: ...

        monkeypatch.setattr(
            "apps.catalog.management.commands.link_ayla_service_ids.CatalogHttpClient",
            _FakeClient,
        )

    def test_command_dry_run(self, tenant: Tenant, monkeypatch, capsys) -> None:
        _legacy_row(tenant, name="Маникюр", slug="manikyur")
        self._patch_client(monkeypatch, [_dto(name="Маникюр")])

        call_command("link_ayla_service_ids", "--tenant-slug", "penza-pilot")

        out = capsys.readouterr().out
        assert "penza-pilot" in out
        assert CatalogService.all_tenants.get(slug="manikyur").ayla_service_id is None

    def test_command_apply_and_fail_under(self, tenant: Tenant, monkeypatch, capsys) -> None:
        _legacy_row(tenant, name="Маникюр", slug="manikyur")
        _legacy_row(tenant, name="Солярий", slug="solyariy")
        self._patch_client(monkeypatch, [_dto(name="Маникюр")])

        with pytest.raises(CommandError):
            call_command(
                "link_ayla_service_ids",
                "--tenant-slug",
                "penza-pilot",
                "--apply",
                "--fail-under",
                "100",
            )

        out = capsys.readouterr().out
        assert "50.0%" in out
        row = CatalogService.all_tenants.get(slug="manikyur")
        assert row.ayla_service_id is not None  # applied before the gate tripped

    def test_deactivate_duplicates_requires_apply(self, tenant: Tenant) -> None:
        with pytest.raises(CommandError):
            call_command(
                "link_ayla_service_ids",
                "--tenant-slug",
                "penza-pilot",
                "--deactivate-duplicates",
            )
