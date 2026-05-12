"""booking_completed signal handler tests (DRF-534 / Sprint 6 / P8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.identity.models import BotUser, ClientProfile
from apps.identity.signals import booking_completed
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@dataclass
class _Visit:
    visited_at: datetime
    amount: Decimal


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="p8-test", name="P8 test")


@pytest.fixture
def bot_user(tenant):
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="p8-uid",
    )


def _visits(n: int) -> list[_Visit]:
    now = datetime.now(UTC)
    return [
        _Visit(visited_at=now - timedelta(days=i * 30), amount=Decimal("2000")) for i in range(n)
    ]


class TestHappyPath:
    def test_signal_recomputes_profile(self, bot_user):
        booking_completed.send(sender=None, bot_user=bot_user, visits=_visits(5))

        profile = ClientProfile.all_tenants.get(bot_user=bot_user)
        assert profile.frequency_visits == 5
        assert profile.monetary_total == Decimal("10000")
        assert profile.last_recomputed_at is not None

    def test_signal_idempotent(self, bot_user):
        booking_completed.send(sender=None, bot_user=bot_user, visits=_visits(3))
        first_count = ClientProfile.all_tenants.filter(bot_user=bot_user).count()

        booking_completed.send(sender=None, bot_user=bot_user, visits=_visits(5))
        second_count = ClientProfile.all_tenants.filter(bot_user=bot_user).count()

        assert first_count == 1
        assert second_count == 1


class TestTenantScopeHandling:
    def test_works_without_caller_scope(self, bot_user):
        # No tenant_scope active — handler should enter scope on its own.
        booking_completed.send(sender=None, bot_user=bot_user, visits=_visits(2))
        profile = ClientProfile.all_tenants.get(bot_user=bot_user)
        assert profile.frequency_visits == 2

    def test_works_inside_caller_scope(self, bot_user, tenant):
        # Caller already in scope (Phase 1 booking.save path) — handler reuses it.
        with tenant_scope(tenant):
            booking_completed.send(sender=None, bot_user=bot_user, visits=_visits(2))
        profile = ClientProfile.all_tenants.get(bot_user=bot_user)
        assert profile.frequency_visits == 2


class TestDefensive:
    def test_missing_bot_user_logs_and_bails(self):
        # No exception, no DB write.
        booking_completed.send(sender=None, visits=_visits(1))
        assert ClientProfile.all_tenants.count() == 0

    def test_missing_visits_logs_and_bails(self, bot_user):
        # Profile already exists via P1 auto-create — record current state.
        before = ClientProfile.all_tenants.get(bot_user=bot_user)
        before_ts = before.last_recomputed_at

        booking_completed.send(sender=None, bot_user=bot_user)  # no visits=

        after = ClientProfile.all_tenants.get(bot_user=bot_user)
        # last_recomputed_at unchanged — handler didn't run recompute.
        assert after.last_recomputed_at == before_ts

    def test_recompute_failure_does_not_propagate(self, bot_user):
        # Patching recompute_profile to raise — handler must swallow.
        with patch(
            "apps.identity.services.recompute.recompute_profile",
            side_effect=RuntimeError("synthetic"),
        ):
            # Must not raise.
            booking_completed.send(sender=None, bot_user=bot_user, visits=_visits(2))

    def test_recompute_failure_logged(self, bot_user, caplog):
        import logging

        caplog.set_level(logging.ERROR, logger="apps.identity.signals")
        with patch(
            "apps.identity.services.recompute.recompute_profile",
            side_effect=RuntimeError("kaboom"),
        ):
            booking_completed.send(sender=None, bot_user=bot_user, visits=_visits(1))
        assert any("recompute_failed" in record.message for record in caplog.records)


class TestSignalRegistration:
    def test_handler_connected(self):
        # Receivers list non-empty at module load — apps.config.ready()
        # imports signals.py which fires the @receiver decorator.
        # Implicit check: all the other tests in this file would fail
        # if the handler weren't connected (recompute wouldn't run).
        assert len(booking_completed.receivers) >= 1
