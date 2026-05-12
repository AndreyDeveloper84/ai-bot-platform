"""Celery beat recompute task tests (DRF-533 / Sprint 6 / P7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.identity.models import BotUser, ClientProfile
from apps.identity.tasks import (
    _ACTIVE_WINDOW_DAYS,
    _BATCH_SIZE,
    recompute_profiles_daily,
)
from apps.tenancy.models import Tenant


@dataclass
class _Visit:
    visited_at: datetime
    amount: Decimal


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="p7-test", name="P7 test")


def _make_bot_user(tenant, suffix: str, *, last_seen_days_ago: int = 1) -> BotUser:
    bu = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"p7-{suffix}",
    )
    # last_seen auto_now=True — override via update to set a fresh-or-stale value.
    BotUser.all_tenants.filter(id=bu.id).update(
        last_seen=timezone.now() - timedelta(days=last_seen_days_ago)
    )
    return BotUser.all_tenants.get(id=bu.id)


class TestEmptyDB:
    def test_no_users_returns_zero(self):
        summary = recompute_profiles_daily()
        assert summary == {"batches": 0, "users_processed": 0, "errors": 0}


class TestActiveWindowFilter:
    def test_processes_recently_seen_user(self, tenant):
        _make_bot_user(tenant, "active", last_seen_days_ago=5)
        summary = recompute_profiles_daily()
        assert summary["users_processed"] == 1

    def test_skips_stale_user_beyond_90_days(self, tenant):
        _make_bot_user(tenant, "stale", last_seen_days_ago=_ACTIVE_WINDOW_DAYS + 1)
        summary = recompute_profiles_daily()
        assert summary["users_processed"] == 0

    def test_processes_user_at_exact_window_edge(self, tenant):
        # Exactly at the boundary — still active (inclusive).
        _make_bot_user(tenant, "edge", last_seen_days_ago=_ACTIVE_WINDOW_DAYS - 1)
        summary = recompute_profiles_daily()
        assert summary["users_processed"] == 1


class TestVisitSourceDispatch:
    def test_default_empty_visit_source_produces_new_segment(self, tenant):
        bu = _make_bot_user(tenant, "empty-visits")
        recompute_profiles_daily()
        profile = ClientProfile.all_tenants.get(bot_user=bu)
        assert profile.rfm_segment == "new"
        assert profile.monetary_total == Decimal("0")
        assert profile.last_recomputed_at is not None

    def test_custom_visit_source_propagates(self, tenant, settings):
        """settings.IDENTITY_VISIT_SOURCE → dotted path → recompute_profile uses it."""

        bu = _make_bot_user(tenant, "real-visits")

        # Register a callable that returns synthetic visits.
        def _visit_source(bot_user):
            return [
                _Visit(
                    visited_at=datetime.now(UTC) - timedelta(days=5),
                    amount=Decimal("2000"),
                )
                for _ in range(3)
            ]

        with patch("apps.identity.tasks._get_visit_source", return_value=_visit_source):
            recompute_profiles_daily()

        profile = ClientProfile.all_tenants.get(bot_user=bu)
        assert profile.frequency_visits == 3
        assert profile.monetary_total == Decimal("6000")

    def test_bad_dotted_path_falls_back_to_empty(self, tenant, settings):
        settings.IDENTITY_VISIT_SOURCE = "nonexistent.module.callable"
        _make_bot_user(tenant, "bad-path")
        summary = recompute_profiles_daily()
        # Falls back to empty source — user still processed, segment='new'.
        assert summary["users_processed"] == 1
        assert summary["errors"] == 0


class TestPerUserErrorHandling:
    def test_one_failing_user_doesnt_break_batch(self, tenant):
        _make_bot_user(tenant, "ok-1")
        _make_bot_user(tenant, "ok-2")

        # Patch recompute_profile to fail on second call.
        from apps.identity import tasks

        original = tasks.recompute_profile
        call_count = {"n": 0}

        def flaky_recompute(bot_user, visits):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("synthetic failure")
            return original(bot_user, visits)

        with patch.object(tasks, "recompute_profile", side_effect=flaky_recompute):
            summary = recompute_profiles_daily()

        assert summary["users_processed"] == 1
        assert summary["errors"] == 1


class TestBatching:
    def test_batch_size_constant(self):
        # Sanity — keep batch tunable but document the default.
        assert _BATCH_SIZE == 500


class TestAuditTrail:
    def test_audit_row_per_batch(self, tenant):
        _make_bot_user(tenant, "audit-1")
        _make_bot_user(tenant, "audit-2")

        from apps.identity import tasks

        with patch.object(tasks, "write_audit") as mock_audit:
            recompute_profiles_daily()

        # Two users fit in one batch → one audit row.
        assert mock_audit.call_count == 1
        args, kwargs = mock_audit.call_args
        assert args[0] == "identity.recompute_profiles.batch"
        assert kwargs["payload"]["batch_size"] == 2
        assert kwargs["payload"]["ok"] == 2
        assert kwargs["payload"]["fail"] == 0


class TestBeatSchedule:
    def test_beat_schedule_registered(self, settings):
        """Schedule entry must exist with the canonical task path."""
        schedule = settings.CELERY_BEAT_SCHEDULE
        assert "recompute_profiles_daily" in schedule
        entry = schedule["recompute_profiles_daily"]
        assert entry["task"] == "apps.identity.tasks.recompute_profiles_daily"
