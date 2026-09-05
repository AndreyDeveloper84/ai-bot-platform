"""The catalog mirror stops refreshing and somebody is told (DRF-1494).

The pilot ran twelve days on a catalog frozen at 2026-08-23 — 94 mirrored
services against 265 upstream — and the owner found it, not the platform.
These tests cover the half of that defect that is about noticing.

Every "no alarm" assertion below is preceded by a presence assertion on the
same data: the check must be shown to have LOOKED at the tenant before its
silence is allowed to mean anything. A staleness alarm that examines an
empty tenant list is silent too, and a silent alarm is what we are here to
replace.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.catalog.staleness import stale_tenants, sync_ages
from apps.catalog.tasks import alert_stale_catalog_sync
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.tenancy.models import Tenant

THRESHOLD = 3600


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def salon(db) -> Tenant:
    Tenant.objects.all().delete()
    return Tenant.objects.create(slug="formula-tela", name="Formula Tela")


def _age(tenant: Tenant, **delta) -> None:
    tenant.last_catalog_sync_ok_at = timezone.now() - timedelta(**delta)
    tenant.save(update_fields=["last_catalog_sync_ok_at"])


class TestThreshold:
    """The alarm fires past the threshold and is quiet inside it.

    Both directions are asserted over the SAME tenant with the same
    fixtures, so neither result can be an artefact of an empty table.
    """

    def test_twelve_day_freeze_is_stale(self, salon: Tenant) -> None:
        _age(salon, days=12)

        stale = stale_tenants()

        assert [t.slug for t in stale] == ["formula-tela"]
        assert stale[0].age_human == "12d00h"

    def test_fresh_sync_is_not_stale(self, salon: Tenant) -> None:
        _age(salon, minutes=3)

        examined = sync_ages()

        # Presence first: prove the check actually saw this tenant, with a
        # real measured age. Without this line the assertion below would
        # pass just as happily against a table the fixture never populated.
        assert [t.slug for t in examined] == ["formula-tela"]
        assert examined[0].age_seconds is not None and examined[0].age_seconds < 300

        assert [t.slug for t in stale_tenants()] == []

    def test_just_inside_threshold_is_quiet(self, salon: Tenant) -> None:
        _age(salon, seconds=THRESHOLD - 120)

        examined = sync_ages()
        assert [t.slug for t in examined] == ["formula-tela"]
        assert examined[0].age_seconds is not None

        assert [t.slug for t in stale_tenants()] == []

    def test_just_outside_threshold_fires(self, salon: Tenant) -> None:
        _age(salon, seconds=THRESHOLD + 120)

        assert [t.slug for t in stale_tenants()] == ["formula-tela"]

    def test_never_synced_counts_as_stale(self, salon: Tenant) -> None:
        # NULL is not "fine until proven otherwise". To the client asking
        # about a manicure, a salon that never synced and one that stopped
        # syncing are the same outage.
        assert salon.last_catalog_sync_ok_at is None

        stale = stale_tenants()

        assert [t.slug for t in stale] == ["formula-tela"]
        assert stale[0].age_human == "never"


class TestGlobalBotExcluded:
    def test_sentinel_is_not_reported(self, db) -> None:
        Tenant.objects.all().delete()
        Tenant.objects.create(slug=GLOBAL_BOT_TENANT_SLUG, name="Global")
        real = Tenant.objects.create(slug="formula-tela", name="Formula Tela")
        _age(real, days=12)

        reported = {t.slug for t in sync_ages()}

        # Presence: the real salon IS in the report, so the sentinel's
        # absence below is an exclusion and not an empty query.
        assert "formula-tela" in reported
        assert GLOBAL_BOT_TENANT_SLUG not in reported


class TestPaging:
    def test_stale_tenant_pages_the_oncall_channel(self, salon: Tenant) -> None:
        _age(salon, days=12)

        with patch("apps.catalog.tasks.page", return_value=True) as paged:
            counters = alert_stale_catalog_sync()

        assert counters == {"checked": 1, "stale": 1, "paged": 1}
        (severity, title, body), kwargs = paged.call_args
        # `error` and not `warning`: a muted line is the wrong home for a bot
        # that is confidently denying services the salon sells.
        assert severity == "error"
        assert "formula-tela" in title
        assert "12d" in title
        assert kwargs["dedup_key"] == "catalog_sync_stale:formula-tela"
        # The page has to carry the next action, or the operator reads it and
        # goes looking for the runbook that does not exist.
        assert "sync_catalog --tenant formula-tela" in body

    def test_fresh_tenant_pages_nobody(self, salon: Tenant) -> None:
        _age(salon, minutes=3)

        with patch("apps.catalog.tasks.page", return_value=True) as paged:
            counters = alert_stale_catalog_sync()

        # Presence first, on the same run that produced the silence: the task
        # examined exactly one tenant. A task that examined none is silent for
        # the wrong reason, and this assertion is what separates the two.
        assert counters["checked"] == 1
        assert counters["stale"] == 0
        assert paged.call_args_list == []

    def test_paged_counts_only_alerts_that_left_the_process(self, salon: Tenant) -> None:
        # `page()` returns False when it dedups or when every sink is down.
        # The counter must report what was sent, not what we wished to send.
        _age(salon, days=12)

        with patch("apps.catalog.tasks.page", return_value=False) as paged:
            counters = alert_stale_catalog_sync()

        assert paged.call_count == 1
        assert counters == {"checked": 1, "stale": 1, "paged": 0}
