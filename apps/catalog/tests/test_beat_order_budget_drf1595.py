"""Fan-out order and wait budget for the catalog beat (DRF-1595).

### The incident these tests are the memory of

Eight pilot salons synced minutes ago; ``formula-tela`` (the head salon) and
``fevralskiy-svet`` had not synced in three days. The beat line read
``run=8 skipped=0 failed=2`` every fifteen minutes, and for three days
nobody could tell from it that the two failures were Ayla's rate limiter
rather than two broken salons.

Two causes, both here:

* ``Tenant.objects.all().iterator()`` — no ``order_by``. The database's
  order is arbitrary but *stable*, so whoever sits at the tail when the
  quota runs out sits at the tail forever. Fairness under scarcity is not a
  nicety; it is the difference between "some salons are late" and "these two
  salons are dead".
* Throttling was counted as failure, so the journal could not distinguish
  "we stood down" from "it broke" — and an operator watching ``failed=2``
  had no reason to suspect a rate limit.

The tests below are behavioural on purpose: they assert the ORDER tenants
are served in and the COUNTERS the run reports, not the shape of a
queryset. A structural assertion would have passed on the day of the
incident too — nothing about the old code was malformed, it just had no
opinion about order.

NULL handling deserves one note. On SQLite (the local default) an
unqualified ascending sort already puts NULLs first, so
``test_never_synced_is_served_first`` passing locally is necessary but not
sufficient; on Postgres, which CI and production run, NULLs sort LAST by
default and the explicit ``nulls_first=True`` is the only reason a
never-synced salon is served at all. Read the CI run, not just the local
one, for that clause.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone as dj_timezone

from apps.catalog.services.sync import MirrorCounts, SyncResult
from apps.catalog.services.throttle import ThrottleWaitBudget
from apps.catalog.tasks import sync_catalog_for_all_tenants
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _empty_tenant_table(db):
    """Start from an empty table so the served order is entirely ours."""
    Tenant.objects.all().delete()


def _ok(created: int = 1) -> SyncResult:
    return SyncResult(ran=True, services=MirrorCounts(created=created))


def _served_slugs(run_mock) -> list[str]:
    return [call.args[0].slug for call in run_mock.call_args_list]


class TestServingOrder:
    """Stalest first — the rotation that stops the same salons losing."""

    def test_longest_since_last_success_is_served_first(self) -> None:
        now = dj_timezone.now()
        # Inserted freshest-first, i.e. the exact reverse of the order the
        # beat must use. Under the old `Tenant.objects.all()` the database
        # hands them back in roughly this insertion order — which is how the
        # tail stayed the tail.
        Tenant.objects.create(slug="fresh", name="Fresh", last_catalog_sync_ok_at=now)
        Tenant.objects.create(
            slug="an-hour-late", name="Late", last_catalog_sync_ok_at=now - timedelta(hours=1)
        )
        Tenant.objects.create(
            slug="three-days-late",
            name="Formula",
            last_catalog_sync_ok_at=now - timedelta(days=3),
        )

        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            MockService.return_value.run.return_value = _ok()
            sync_catalog_for_all_tenants()

        assert _served_slugs(MockService.return_value.run) == [
            "three-days-late",
            "an-hour-late",
            "fresh",
        ]

    def test_never_synced_is_served_first_of_all(self) -> None:
        now = dj_timezone.now()
        Tenant.objects.create(slug="fresh", name="Fresh", last_catalog_sync_ok_at=now)
        Tenant.objects.create(
            slug="three-days-late",
            name="Formula",
            last_catalog_sync_ok_at=now - timedelta(days=3),
        )
        # Inserted last, must be served first: never having synced is the
        # most behind there is, not the least. An unqualified ASC on
        # Postgres would bury this row at the end of the walk — exactly
        # where the quota runs out.
        Tenant.objects.create(slug="never-synced", name="New salon", last_catalog_sync_ok_at=None)

        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            MockService.return_value.run.return_value = _ok()
            sync_catalog_for_all_tenants()

        assert _served_slugs(MockService.return_value.run) == [
            "never-synced",
            "three-days-late",
            "fresh",
        ]

    def test_equal_clocks_are_broken_deterministically_by_id(self) -> None:
        """Ties are ordinary, and an untied tie is the bug coming back.

        A contour where several salons were loaded in one go has them all on
        the same clock (or all on NULL). Without a second sort key the
        database decides the order among them — which is precisely the
        "order nobody chose" this ticket is about, reappearing through a
        door we just fixed.
        """
        same_clock = dj_timezone.now() - timedelta(hours=2)
        ids = [
            uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001"),
            uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002"),
            uuid.UUID("cccccccc-0000-4000-8000-000000000003"),
        ]
        # Inserted in reverse id order, so insertion order and id order
        # disagree and only a real tie-break can produce the expectation.
        for index, tenant_id in enumerate(reversed(ids)):
            Tenant.objects.create(
                id=tenant_id,
                slug=f"tied-{index}",
                name=f"Tied {index}",
                last_catalog_sync_ok_at=same_clock,
            )

        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            MockService.return_value.run.return_value = _ok()
            sync_catalog_for_all_tenants()

        served = [call.args[0].id for call in MockService.return_value.run.call_args_list]
        assert served == ids

    def test_order_is_the_same_on_a_repeated_run(self) -> None:
        # Determinism is a claim about repetition, so repeat it: same rows,
        # same clocks, same order, twice.
        same_clock = dj_timezone.now() - timedelta(hours=2)
        for index in range(4):
            Tenant.objects.create(
                slug=f"tied-{index}", name=f"Tied {index}", last_catalog_sync_ok_at=same_clock
            )

        orders = []
        for _ in range(2):
            with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
                MockService.return_value.run.return_value = _ok()
                sync_catalog_for_all_tenants()
                orders.append(_served_slugs(MockService.return_value.run))

        assert orders[0] == orders[1]
        assert len(orders[0]) == 4


class TestWaitBudgetExhaustion:
    """Out of budget ⇒ the rest are SKIPPED, and the log says why."""

    def _four_salons(self) -> None:
        now = dj_timezone.now()
        for index in range(4):
            Tenant.objects.create(
                slug=f"salon-{index}",
                name=f"Salon {index}",
                # Descending staleness so the served order is salon-0 first.
                last_catalog_sync_ok_at=now - timedelta(hours=4 - index),
            )

    def test_remaining_salons_are_skipped_not_failed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._four_salons()
        budget = ThrottleWaitBudget(60)

        def _first_salon_burns_the_budget(tenant: Tenant) -> SyncResult:
            # What the real client does on a 429: reserve what Ayla asked
            # for, and be refused when the run can no longer cover it.
            budget.consume(54)
            assert budget.consume(54) is False
            return SyncResult(ran=False, skipped=True, skip_reason="throttled")

        with patch("apps.catalog.tasks.ThrottleWaitBudget") as MockBudget:
            MockBudget.from_settings.return_value = budget
            with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
                MockService.return_value.run.side_effect = _first_salon_burns_the_budget
                with caplog.at_level(logging.WARNING, logger="apps.catalog.tasks"):
                    counters = sync_catalog_for_all_tenants()

        # Only the first salon was attempted; the other three were not asked
        # at all — walking them would push back the moment the limiter
        # reopens and make the next tick worse than this one.
        assert MockService.return_value.run.call_count == 1
        assert counters["tenants_failed"] == 0
        assert counters["tenants_run"] == 0
        assert counters["tenants_skipped"] == 4
        assert counters["tenants_skipped_throttled"] == 4

        stand_down = [
            r for r in caplog.records if "beat_throttle_budget_exhausted" in r.getMessage()
        ]
        assert len(stand_down) == 1
        message = stand_down[0].getMessage()
        # The salons that lost this cycle are named, because the next
        # question an operator asks is "which ones".
        for slug in ("salon-1", "salon-2", "salon-3"):
            assert slug in message

    def test_throttled_and_failed_are_different_lines_in_the_beat_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The distinction the journal was missing for three days."""
        self._four_salons()
        budget = ThrottleWaitBudget(60)

        results = [
            SyncResult(ran=False, skipped=True, skip_reason="throttled"),
            RuntimeError("a genuinely broken salon"),
            _ok(),
            SyncResult(ran=False, skipped=True, skip_reason="lock_held"),
        ]

        with patch("apps.catalog.tasks.ThrottleWaitBudget") as MockBudget:
            MockBudget.from_settings.return_value = budget
            with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
                MockService.return_value.run.side_effect = results
                with caplog.at_level(logging.INFO, logger="apps.catalog.tasks"):
                    counters = sync_catalog_for_all_tenants()

        assert counters["tenants_run"] == 1
        assert counters["tenants_failed"] == 1
        # Two skips, one of them a throttle: the subset is what tells an
        # operator that the cause is upstream quota, not a beat racing
        # itself on the lock.
        assert counters["tenants_skipped"] == 2
        assert counters["tenants_skipped_throttled"] == 1

        completed = [r for r in caplog.records if "beat_completed" in r.getMessage()]
        assert len(completed) == 1
        assert "skipped_throttled=1" in completed[0].getMessage()
        assert "failed=1" in completed[0].getMessage()

    def test_healthy_run_never_stands_down(self, caplog: pytest.LogCaptureFixture) -> None:
        # The budget is spent only on 429 sleeps, so a cycle with no
        # throttling must be indistinguishable from one before this ticket.
        self._four_salons()
        budget = ThrottleWaitBudget(240)

        with patch("apps.catalog.tasks.ThrottleWaitBudget") as MockBudget:
            MockBudget.from_settings.return_value = budget
            with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
                MockService.return_value.run.return_value = _ok()
                with caplog.at_level(logging.WARNING, logger="apps.catalog.tasks"):
                    counters = sync_catalog_for_all_tenants()

        assert MockService.return_value.run.call_count == 4
        assert counters["tenants_run"] == 4
        assert counters["tenants_skipped_throttled"] == 0
        assert budget.spent_seconds == 0.0
        assert not [r for r in caplog.records if "beat_throttle_budget_exhausted" in r.getMessage()]


class TestSentinelStillExcluded:
    def test_global_bot_is_neither_synced_nor_reported_as_stood_down(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The #1019 exclusion must survive the budget gate.

        The sentinel owns global BotUsers and discovery, not a salon
        catalog; Ayla answers 400 for it every cycle. It must not be synced,
        and — new with DRF-1595 — it must not appear in the stand-down list
        either, or an operator reading the warning would go looking for a
        salon that does not exist.
        """
        # Never synced, so the ordering puts it first and the exclusion is
        # actually exercised rather than skipped over.
        Tenant.objects.create(
            slug=GLOBAL_BOT_TENANT_SLUG, name="Global bot", last_catalog_sync_ok_at=None
        )
        Tenant.objects.create(
            slug="real-salon",
            name="Real",
            last_catalog_sync_ok_at=dj_timezone.now() - timedelta(hours=1),
        )
        budget = ThrottleWaitBudget(0)  # exhausted before the first tenant

        with patch("apps.catalog.tasks.ThrottleWaitBudget") as MockBudget:
            MockBudget.from_settings.return_value = budget
            with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
                MockService.return_value.run.return_value = _ok()
                with caplog.at_level(logging.WARNING, logger="apps.catalog.tasks"):
                    counters = sync_catalog_for_all_tenants()

        assert MockService.return_value.run.call_count == 0
        # One salon stood down, not two: the sentinel is not a salon.
        assert counters["tenants_skipped_throttled"] == 1
        stand_down = [
            r for r in caplog.records if "beat_throttle_budget_exhausted" in r.getMessage()
        ]
        assert len(stand_down) == 1
        assert GLOBAL_BOT_TENANT_SLUG not in stand_down[0].getMessage()
        assert "real-salon" in stand_down[0].getMessage()
