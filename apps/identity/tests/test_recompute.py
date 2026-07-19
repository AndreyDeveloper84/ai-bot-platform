"""Recompute orchestrator tests (DRF-532 / Sprint 6 / P6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.identity.models import BotUser, ClientProfile
from apps.identity.services.recompute import recompute_profile
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@dataclass
class _Visit:
    visited_at: datetime
    amount: Decimal


_AS_OF = datetime(2026, 5, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_now():
    """Pin «now» to _AS_OF — the visits/expectations in this module are
    computed against it (recency_days, RFM bands). Without the freeze the
    whole module time-bombs as real time moves past 2026-05-12.
    ``tick=True`` keeps per-call ordering (idempotency test asserts
    last_recomputed_at advances between recomputes)."""
    from freezegun import freeze_time

    with freeze_time(_AS_OF, tick=True):
        yield


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="p6-test", name="P6 test")


@pytest.fixture
def bot_user(tenant):
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="recompute-uid",
    )


def _visits_at(days_back: list[int], amount=Decimal("2000")) -> list[_Visit]:
    return [_Visit(visited_at=_AS_OF - timedelta(days=d), amount=amount) for d in days_back]


class TestRecomputeHappyPath:
    def test_writes_profile_fields(self, bot_user, tenant):
        # 5 visits, recent (loyal-band) — frequency=5, monetary=10000
        with tenant_scope(tenant):
            profile = recompute_profile(bot_user, _visits_at([5, 30, 60, 90, 120]))

        assert profile.bot_user_id == bot_user.id
        assert profile.tenant_id == tenant.id
        assert profile.frequency_visits == 5
        assert profile.monetary_total == Decimal("10000")
        assert profile.recency_days == 5
        assert profile.rfm_segment == "loyal"
        assert profile.lifecycle_stage in {"active", "lapsing"}
        assert profile.loyalty_tier in {"bronze", "silver"}
        assert profile.last_recomputed_at is not None

    def test_empty_visits_produces_new_segment(self, bot_user, tenant):
        with tenant_scope(tenant):
            profile = recompute_profile(bot_user, [])

        assert profile.frequency_visits == 0
        assert profile.monetary_total == Decimal("0")
        assert profile.rfm_segment == "new"
        assert profile.lifecycle_stage == "new"
        assert profile.loyalty_tier == "bronze"
        assert profile.recency_days is None
        assert profile.avg_visit_interval_days is None


class TestIdempotency:
    def test_second_call_updates_same_row(self, bot_user, tenant):
        with tenant_scope(tenant):
            recompute_profile(bot_user, _visits_at([5]))
            recompute_profile(bot_user, _visits_at([5, 30, 60, 90, 120, 150, 180, 210]))

        # Still one row; second call overwrote first.
        assert ClientProfile.all_tenants.filter(bot_user=bot_user).count() == 1
        profile = ClientProfile.all_tenants.get(bot_user=bot_user)
        # Second call had 8 visits — frequency reflects latest, not sum.
        assert profile.frequency_visits == 8

    def test_last_recomputed_at_updates_per_call(self, bot_user, tenant):
        with tenant_scope(tenant):
            recompute_profile(bot_user, _visits_at([5]))
            first_ts = ClientProfile.all_tenants.get(bot_user=bot_user).last_recomputed_at

        # Force a small delay then recompute again.
        import time

        time.sleep(0.01)
        with tenant_scope(tenant):
            recompute_profile(bot_user, _visits_at([5, 30]))
            second_ts = ClientProfile.all_tenants.get(bot_user=bot_user).last_recomputed_at

        assert second_ts > first_ts


class TestTenantScopeGuard:
    def test_raises_when_outside_tenant_scope(self, bot_user):
        with pytest.raises(ValueError, match="must be called inside tenant_scope"):
            recompute_profile(bot_user, [])

    def test_raises_when_wrong_tenant(self, bot_user, tenant):
        wrong = Tenant.objects.create(slug="p6-wrong", name="P6 wrong")
        with tenant_scope(wrong):
            with pytest.raises(ValueError, match="must be called inside tenant_scope"):
                recompute_profile(bot_user, [])


class TestEventEmission:
    def test_emits_client_profile_recomputed(self, bot_user, tenant):
        with patch("apps.identity.services.recompute.emit") as mock_emit:
            with tenant_scope(tenant):
                recompute_profile(bot_user, _visits_at([5, 30, 60]))

        mock_emit.assert_called_once()
        args, kwargs = mock_emit.call_args
        assert args[0] == "client_profile_recomputed"
        assert kwargs["distinct_id"] == str(bot_user.id)
        properties = kwargs["properties"]
        assert "rfm_segment" in properties
        assert "lifecycle_stage" in properties
        assert "loyalty_tier" in properties


class TestAvgIntervalDerivation:
    def test_two_visits_30_days_apart(self, bot_user, tenant):
        with tenant_scope(tenant):
            profile = recompute_profile(bot_user, _visits_at([0, 30]))
        assert profile.avg_visit_interval_days == 30

    def test_three_visits_15_days_apart(self, bot_user, tenant):
        # span 30, N-1=2, → 15
        with tenant_scope(tenant):
            profile = recompute_profile(bot_user, _visits_at([0, 15, 30]))
        assert profile.avg_visit_interval_days == 15

    def test_single_visit_returns_none(self, bot_user, tenant):
        with tenant_scope(tenant):
            profile = recompute_profile(bot_user, _visits_at([10]))
        assert profile.avg_visit_interval_days is None


class TestPersistence:
    def test_returned_profile_is_saved(self, bot_user, tenant):
        with tenant_scope(tenant):
            profile = recompute_profile(bot_user, _visits_at([5]))
        # Re-read from DB and confirm values match.
        from_db = ClientProfile.all_tenants.get(bot_user=bot_user)
        assert from_db.pk == profile.pk
        assert from_db.frequency_visits == 1
