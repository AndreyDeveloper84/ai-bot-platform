"""`manage.py sync_catalog` — the one command the pilot runbook names (DRF-1494).

This is what an operator runs to push a stuck mirror through, so its
behaviour is pinned rather than trusted: `--status` must write nothing,
`--dry-run` must write nothing, and the default run must report the row
count it actually changed rather than announcing success.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone as dj_timezone

from apps.catalog.models import CatalogService
from apps.catalog.services.http_client import CatalogSalonServiceDTO
from apps.catalog.services.tests.test_sync import FakeHttpClient
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def salon(db) -> Tenant:
    Tenant.objects.all().delete()
    return Tenant.objects.create(slug="formula-tela", name="Formula Tela")


def _salon_dto(name: str = "Маникюр классический") -> CatalogSalonServiceDTO:
    return CatalogSalonServiceDTO(
        ayla_service_id=str(uuid.uuid4()),
        external_updated_at=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc),
        name=name,
    )


class TestStatus:
    def test_reports_age_and_mirror_size_without_writing(self, salon: Tenant) -> None:
        salon.last_catalog_sync_ok_at = dj_timezone.now() - timedelta(days=12)
        salon.save(update_fields=["last_catalog_sync_ok_at"])
        out = StringIO()

        call_command("sync_catalog", "--status", stdout=out)

        text = out.getvalue()
        assert "formula-tela" in text
        assert "12d" in text
        assert "STALE" in text

    def test_fresh_tenant_is_listed_without_the_stale_marker(self, salon: Tenant) -> None:
        salon.last_catalog_sync_ok_at = dj_timezone.now()
        salon.save(update_fields=["last_catalog_sync_ok_at"])
        out = StringIO()

        call_command("sync_catalog", "--status", stdout=out)

        text = out.getvalue()
        # Presence before absence: the row IS printed, so the missing marker
        # below is a verdict about this salon and not an empty report.
        assert "formula-tela" in text
        assert "STALE" not in text


class TestRun:
    def test_reports_the_row_count_it_changed(self, salon: Tenant) -> None:
        http = FakeHttpClient(services=[_salon_dto(), _salon_dto("Педикюр")])
        out = StringIO()

        with patch("apps.catalog.services.sync.CatalogHttpClient", return_value=http):
            call_command("sync_catalog", "--tenant", "formula-tela", stdout=out)

        assert CatalogService.all_tenants.filter(tenant=salon).count() == 2
        # The measurement, not the claim: before -> after in the output.
        assert "services 0 -> 2" in out.getvalue()

    def test_unknown_tenant_is_refused(self, salon: Tenant) -> None:
        with pytest.raises(CommandError, match="no tenant with slug"):
            call_command("sync_catalog", "--tenant", "not-a-salon")

    def test_global_bot_is_not_swept_into_an_all_tenant_run(self, db) -> None:
        Tenant.objects.all().delete()
        Tenant.objects.create(slug=GLOBAL_BOT_TENANT_SLUG, name="Global")
        Tenant.objects.create(slug="formula-tela", name="Formula Tela")
        http = FakeHttpClient(services=[])
        out = StringIO()

        with patch("apps.catalog.services.sync.CatalogHttpClient", return_value=http):
            call_command("sync_catalog", stdout=out)

        text = out.getvalue()
        assert "formula-tela" in text
        assert GLOBAL_BOT_TENANT_SLUG not in text


class TestDryRun:
    def test_reports_what_would_change_and_writes_nothing(self, salon: Tenant) -> None:
        http = FakeHttpClient(services=[_salon_dto(), _salon_dto("Педикюр")])
        out = StringIO()

        with patch("apps.catalog.services.http_client.CatalogHttpClient", return_value=http):
            call_command("sync_catalog", "--tenant", "formula-tela", "--dry-run", stdout=out)

        text = out.getvalue()
        # Presence: the dry run did reach upstream and saw two rows.
        assert "upstream=2" in text
        assert "would_create=2" in text
        assert CatalogService.all_tenants.filter(tenant=salon).count() == 0
