"""``link_ayla_service_ids`` tests — C6/AMD-001 catalog linking.

The matching engine (:mod:`apps.catalog.services.linking`) back-fills
``CatalogService.ayla_service_id`` for legacy (NULL-link) rows per the
C6 contract (PILOT_CONTRACTS AMD-001):

* auto-match on the **pair** (category_slug, normalized name) — bot side
  uses the row's ``slug`` as its category slug;
* normalization: lower, trim, ё→е, collapse whitespace, strip «ёлочки»;
* **duration** breaks ties when a pair is ambiguous (unique match only);
* a **mapping file** (JSON) supplies manual correspondences for rows
  that cannot auto-match;
* coverage report buckets: ``matched auto`` / ``matched manual`` /
  ``unmatched``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import CatalogService
from apps.catalog.services.http_client import CatalogSalonServiceDTO
from apps.catalog.services.linking import (
    extract_template_slug,
    link_tenant_services,
    normalize_name,
)
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="penza-pilot", name="Penza Pilot")


def _dto(
    ayla_service_id: str | None = None,
    *,
    name: str = "Маникюр",
    category: str | None = "manikyur",
    duration_min: int | None = 60,
    template: str | None = "9d3f0000-0000-4000-8000-000000000002",
    raw: dict | None = None,
) -> CatalogSalonServiceDTO:
    aid = ayla_service_id or str(uuid.uuid4())
    base_raw = {"id": aid}
    if template is not None:
        base_raw["template"] = template
    return CatalogSalonServiceDTO(
        ayla_service_id=aid,
        external_updated_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        name=name,
        category=category,
        duration_min=duration_min,
        template=template,
        raw=raw if raw is not None else base_raw,
    )


def _legacy_row(
    tenant: Tenant,
    *,
    name: str,
    slug: str,
    duration_min: int | None = 60,
    is_active: bool = True,
) -> CatalogService:
    """A pre-S3B catalog row: legacy integer external_id, NULL Ayla link."""
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=abs(hash(slug)) % 10_000_000,
        external_updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        name=name,
        slug=slug,
        duration_min=duration_min,
        is_active=is_active,
    )


class TestNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Маникюр   Аппаратный ", "маникюр аппаратный"),
            ("Моделирование вёдер", "моделирование ведер"),
            ("Массаж «лимфодренажный» ручной", "массаж лимфодренажный ручной"),
            ("SPA-комплекс «Антистресс»", "spa-комплекс антистресс"),
        ],
    )
    def test_rules(self, raw: str, expected: str) -> None:
        assert normalize_name(raw) == expected


class TestPairMatching:
    def test_pair_match_links(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Маникюр классический", slug="manikyur")
        aid = str(uuid.uuid4())
        dto = _dto(aid, name="Маникюр классический", category="manikyur")

        report = link_tenant_services(tenant, [dto], apply=True)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid
        assert len(report.matched_auto) == 1
        assert report.matched_auto[0].matched_by == "pair"

    def test_pair_requires_both_keys(self, tenant: Tenant) -> None:
        """Name alone is not enough — category must match too."""
        row = _legacy_row(tenant, name="Маникюр классический", slug="manikyur")
        dto = _dto(name="Маникюр классический", category="pedicure")

        report = link_tenant_services(tenant, [dto], apply=True)

        row.refresh_from_db()
        assert row.ayla_service_id is None
        assert len(report.unmatched) == 1

    def test_guillemets_ignored_in_names(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Массаж «лимфодренажный»", slug="massage")
        aid = str(uuid.uuid4())
        dto = _dto(aid, name="Массаж лимфодренажный", category="massage")

        link_tenant_services(tenant, [dto], apply=True)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid

    def test_ambiguous_pair_needs_duration(self, tenant: Tenant) -> None:
        """Two Ayla rows share the pair → duration picks the unique one."""
        row = _legacy_row(tenant, name="Маникюр", slug="manikyur", duration_min=90)
        aid_60 = str(uuid.uuid4())
        aid_90 = str(uuid.uuid4())
        dtos = [
            _dto(aid_60, name="Маникюр", category="manikyur", duration_min=60),
            _dto(aid_90, name="Маникюр", category="manikyur", duration_min=90),
        ]

        report = link_tenant_services(tenant, dtos, apply=True)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid_90
        assert report.matched_auto[0].matched_by == "pair+duration"

    def test_ambiguous_pair_no_duration_no_match(self, tenant: Tenant) -> None:
        """Pair ambiguous and duration can't disambiguate → unmatched."""
        row = _legacy_row(tenant, name="Маникюр", slug="manikyur", duration_min=45)
        dtos = [
            _dto(name="Маникюр", category="manikyur", duration_min=60),
            _dto(name="Маникюр", category="manikyur", duration_min=90),
        ]

        report = link_tenant_services(tenant, dtos, apply=True)

        row.refresh_from_db()
        assert row.ayla_service_id is None
        assert len(report.unmatched) == 1


class TestDurationEdgeCases:
    def test_unique_pair_ignores_duration_mismatch(self, tenant: Tenant) -> None:
        """Duration is only a tiebreaker — a unique pair match links even
        when durations differ (Ayla duration is canonical anyway)."""
        row = _legacy_row(tenant, name="Маникюр", slug="manikyur", duration_min=45)
        aid = str(uuid.uuid4())

        report = link_tenant_services(tenant, [_dto(aid, duration_min=60)], apply=True)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid
        assert report.matched_auto[0].matched_by == "pair"

    def test_null_bot_duration_ambiguous_pair(self, tenant: Tenant) -> None:
        _legacy_row(tenant, name="Маникюр", slug="manikyur", duration_min=None)
        dtos = [
            _dto(name="Маникюр", category="manikyur", duration_min=60),
            _dto(name="Маникюр", category="manikyur", duration_min=90),
        ]

        report = link_tenant_services(tenant, dtos, apply=True)

        assert len(report.unmatched) == 1


class TestMappingFile:
    def test_manual_entry_links(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Экзотика", slug="exotic")
        aid = str(uuid.uuid4())
        dto = _dto(aid, name="Экзотика plus", category="other")
        mapping = {aid: {"tenant_slug": "penza-pilot", "service_slug": "exotic"}}

        report = link_tenant_services(tenant, [dto], apply=True, manual_map=mapping)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid
        assert len(report.matched_manual) == 1
        assert report.matched_manual[0].matched_by == "manual"
        assert report.unmatched == []

    def test_manual_entry_by_template_id(self, tenant: Tenant) -> None:
        """Mapping file keys may be ayla template_id (C6 discovery key)."""
        row = _legacy_row(tenant, name="Экзотика", slug="exotic")
        aid = str(uuid.uuid4())
        tid = "9d3f0000-0000-4000-8000-000000000099"
        dto = _dto(aid, name="Экзотика plus", category="other", template=tid)
        mapping = {tid: {"tenant_slug": "penza-pilot", "service_slug": "exotic"}}

        report = link_tenant_services(tenant, [dto], apply=True, manual_map=mapping)

        row.refresh_from_db()
        assert str(row.ayla_service_id) == aid
        assert len(report.matched_manual) == 1

    def test_manual_unknown_ayla_id_ignored_with_note(self, tenant: Tenant) -> None:
        """A manual entry pointing at an ayla id the upstream does not
        return is skipped and reported (never a blind stamp)."""
        _legacy_row(tenant, name="Экзотика", slug="exotic")
        ghost = str(uuid.uuid4())
        mapping = {ghost: {"tenant_slug": "penza-pilot", "service_slug": "exotic"}}

        report = link_tenant_services(tenant, [_dto()], apply=True, manual_map=mapping)

        assert report.matched_manual == []
        assert report.manual_skipped == [ghost]

    def test_manual_entry_unknown_service_slug(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        mapping = {aid: {"tenant_slug": "penza-pilot", "service_slug": "nope"}}

        report = link_tenant_services(tenant, [_dto(aid)], apply=True, manual_map=mapping)

        assert report.matched_manual == []


class TestDryRunAndReport:
    def test_dry_run_reports_without_writing(self, tenant: Tenant) -> None:
        row = _legacy_row(tenant, name="Маникюр", slug="manikyur")

        report = link_tenant_services(tenant, [_dto()], apply=False)

        row.refresh_from_db()
        assert row.ayla_service_id is None
        assert len(report.matched_auto) == 1

    def test_coverage_buckets(self, tenant: Tenant) -> None:
        _legacy_row(tenant, name="Маникюр", slug="manikyur")
        _legacy_row(tenant, name="Экзотика", slug="exotic")
        _legacy_row(tenant, name="Солярий", slug="solyariy")
        aid = str(uuid.uuid4())
        aid2 = str(uuid.uuid4())
        dtos = [
            _dto(aid, name="Маникюр", category="manikyur"),
            _dto(aid2, name="Экзотика plus", category="other"),
        ]
        mapping = {aid2: {"tenant_slug": "penza-pilot", "service_slug": "exotic"}}

        report = link_tenant_services(tenant, dtos, apply=True, manual_map=mapping)

        assert len(report.matched_auto) == 1
        assert len(report.matched_manual) == 1
        assert len(report.unmatched) == 1
        assert report.coverage_after == pytest.approx(2 / 3)


class TestDuplicatesAndScope:
    def test_existing_ayla_row_blocks_stamp(self, tenant: Tenant) -> None:
        legacy = _legacy_row(tenant, name="Маникюр", slug="manikyur")
        aid = str(uuid.uuid4())
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
        assert len(report.duplicates) == 1

    def test_already_linked_rows_untouched(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            name="Маникюр",
            slug="manikyur",
            ayla_service_id=aid,
        )

        report = link_tenant_services(tenant, [_dto()], apply=True)

        assert report.matched_auto == []
        assert report.unmatched == []

    def test_inactive_rows_ignored(self, tenant: Tenant) -> None:
        _legacy_row(tenant, name="Маникюр", slug="manikyur", is_active=False)

        report = link_tenant_services(tenant, [_dto()], apply=True)

        assert report.active_before == 0
        assert report.unmatched == []

    def test_extract_template_slug_forward_compat(self) -> None:
        """Slug extraction rides raw payload keys when W1 ships one."""
        dto = _dto(raw={"id": "x", "template_slug": "manikyur"})
        assert extract_template_slug(dto) == "manikyur"
        dto2 = _dto(raw={"id": "x", "template": {"id": "y", "slug": "pedi"}})
        assert extract_template_slug(dto2) == "pedi"


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
        self._patch_client(monkeypatch, [_dto()])

        call_command("link_ayla_service_ids", "--tenant-slug", "penza-pilot")

        out = capsys.readouterr().out
        assert "penza-pilot" in out
        assert "matched auto" in out
        assert CatalogService.all_tenants.get(slug="manikyur").ayla_service_id is None

    def test_command_mapping_file(self, tenant: Tenant, monkeypatch, capsys, tmp_path) -> None:
        _legacy_row(tenant, name="Экзотика", slug="exotic")
        aid = str(uuid.uuid4())
        self._patch_client(monkeypatch, [_dto(aid, name="Экзотика plus", category="other")])
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(
            json.dumps({aid: {"tenant_slug": "penza-pilot", "service_slug": "exotic"}}),
            encoding="utf-8",
        )

        call_command(
            "link_ayla_service_ids",
            "--tenant-slug",
            "penza-pilot",
            "--apply",
            "--mapping-file",
            str(mapping_file),
        )

        out = capsys.readouterr().out
        assert "matched manual" in out
        row = CatalogService.all_tenants.get(slug="exotic")
        assert str(row.ayla_service_id) == aid

    def test_command_apply_and_fail_under(self, tenant: Tenant, monkeypatch, capsys) -> None:
        _legacy_row(tenant, name="Маникюр", slug="manikyur")
        _legacy_row(tenant, name="Солярий", slug="solyariy")
        self._patch_client(monkeypatch, [_dto()])

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

    def test_bad_mapping_file_rejected(self, tenant: Tenant, tmp_path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("[1,2,3]", encoding="utf-8")
        with pytest.raises(CommandError):
            call_command(
                "link_ayla_service_ids",
                "--tenant-slug",
                "penza-pilot",
                "--mapping-file",
                str(bad),
            )
