"""Per-tenant cost tracker tests (Phase 1 / PI9 / DRF-860).

Covers:
  * record_usage / get_current_usage round-trip.
  * Cap enforcement (token + cost, independent).
  * Pre-emptive projected_tokens guard.
  * Cross-80% and cross-100% threshold alerts with dedup.
  * UTC midnight rollover via freezegun.
  * Multi-tenant isolation.
  * Empty manager_chat_id → log + skip, cap still enforced.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.cache import cache
from freezegun import freeze_time

from apps.llm.cost_tracker import (
    TenantQuotaExceeded,
    enforce_caps,
    get_current_usage,
    record_usage,
)
from apps.tenancy.models import Tenant

# Audit + event writes need transaction-mode SQLite — sync_to_async
# bridges into a worker thread which can't share the test's in-memory
# in-flight transaction.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def tenant():
    return Tenant.objects.create(
        slug="pi9-tenant",
        name="PI9 Tenant",
        daily_token_cap=10_000,
        daily_cost_cap_usd=Decimal("5.00"),
    )


@pytest.fixture
def tenant_with_manager():
    return Tenant.objects.create(
        slug="pi9-tenant-mgr",
        name="PI9 Tenant w/ manager",
        daily_token_cap=10_000,
        daily_cost_cap_usd=Decimal("5.00"),
        manager_chat_id="manager-chat-42",
    )


# ---------------------------------------------------------------------------
# record_usage + get_current_usage
# ---------------------------------------------------------------------------


class TestRecordUsageBasic:
    async def test_first_call_sets_keys_with_ttl(self, tenant: Tenant) -> None:
        await record_usage(str(tenant.id), tokens=120, cost_usd=Decimal("0.02"))
        usage = await get_current_usage(str(tenant.id))
        assert usage.tokens_used == 120
        assert usage.cost_used_usd == Decimal("0.02")

    async def test_subsequent_call_accumulates(self, tenant: Tenant) -> None:
        await record_usage(str(tenant.id), tokens=100, cost_usd=Decimal("0.01"))
        await record_usage(str(tenant.id), tokens=50, cost_usd=Decimal("0.005"))
        usage = await get_current_usage(str(tenant.id))
        assert usage.tokens_used == 150
        assert usage.cost_used_usd == Decimal("0.015")

    async def test_get_current_usage_returns_zero_when_no_calls(self, tenant: Tenant) -> None:
        usage = await get_current_usage(str(tenant.id))
        assert usage.tokens_used == 0
        assert usage.cost_used_usd == Decimal("0")
        assert usage.percent_of_token_cap == 0.0
        assert usage.percent_of_cost_cap == 0.0

    async def test_percent_calculations(self, tenant: Tenant) -> None:
        # 2500 / 10_000 = 25%, 1.25 / 5.00 = 25%.
        await record_usage(str(tenant.id), tokens=2_500, cost_usd=Decimal("1.25"))
        usage = await get_current_usage(str(tenant.id))
        assert pytest.approx(usage.percent_of_token_cap, abs=0.001) == 0.25
        assert pytest.approx(usage.percent_of_cost_cap, abs=0.001) == 0.25


# ---------------------------------------------------------------------------
# enforce_caps — token cap
# ---------------------------------------------------------------------------


class TestEnforceTokenCap:
    async def test_under_cap_passes(self, tenant: Tenant) -> None:
        await record_usage(str(tenant.id), tokens=100, cost_usd=Decimal("0"))
        await enforce_caps(str(tenant.id))  # no raise

    async def test_at_cap_raises(self, tenant: Tenant) -> None:
        await record_usage(str(tenant.id), tokens=10_000, cost_usd=Decimal("0"))
        with pytest.raises(TenantQuotaExceeded) as exc_info:
            await enforce_caps(str(tenant.id))
        assert exc_info.value.which_cap == "token"
        assert exc_info.value.cap_value == 10_000
        assert exc_info.value.current_value >= 10_000

    async def test_projected_tokens_preemptive_reject(self, tenant: Tenant) -> None:
        # 9500 used + 600 projected > 10_000 cap → pre-emptive reject.
        await record_usage(str(tenant.id), tokens=9_500, cost_usd=Decimal("0"))
        with pytest.raises(TenantQuotaExceeded) as exc_info:
            await enforce_caps(str(tenant.id), projected_tokens=600)
        assert exc_info.value.which_cap == "token"

    async def test_projected_tokens_within_budget_passes(self, tenant: Tenant) -> None:
        # 9500 used + 400 projected ≤ 10_000 → allowed.
        await record_usage(str(tenant.id), tokens=9_500, cost_usd=Decimal("0"))
        await enforce_caps(str(tenant.id), projected_tokens=400)  # no raise


# ---------------------------------------------------------------------------
# enforce_caps — cost cap
# ---------------------------------------------------------------------------


class TestEnforceCostCap:
    async def test_cost_cap_raises_with_which_cap_cost(self, tenant: Tenant) -> None:
        # Tokens stay low (well under 10k), cost burns through $5 budget.
        await record_usage(str(tenant.id), tokens=100, cost_usd=Decimal("5.00"))
        with pytest.raises(TenantQuotaExceeded) as exc_info:
            await enforce_caps(str(tenant.id))
        assert exc_info.value.which_cap == "cost"
        # cap_value should be Decimal("5.00").
        assert exc_info.value.cap_value == Decimal("5.00")

    async def test_token_cap_takes_precedence_when_both_exhausted(self, tenant: Tenant) -> None:
        # Both caps exhausted. enforce_caps checks token first, so the
        # caller sees which_cap="token" — keeps the orchestrator audit
        # row deterministic.
        await record_usage(str(tenant.id), tokens=20_000, cost_usd=Decimal("10.00"))
        with pytest.raises(TenantQuotaExceeded) as exc_info:
            await enforce_caps(str(tenant.id))
        assert exc_info.value.which_cap == "token"


# ---------------------------------------------------------------------------
# Threshold-crossing alerts
# ---------------------------------------------------------------------------


@pytest.fixture
def _patched_send_message():
    with patch("apps.channels.max.outbound.send_message") as mock:
        mock.return_value = {"ok": True}
        yield mock


class TestThresholdAlerts:
    async def test_cross_80_fires_one_alert(
        self,
        tenant_with_manager: Tenant,
        _patched_send_message,
    ) -> None:
        # From 79% (7900) to 81% (8100) — single boundary cross.
        await record_usage(str(tenant_with_manager.id), tokens=7_900, cost_usd=Decimal("0"))
        _patched_send_message.assert_not_called()
        await record_usage(str(tenant_with_manager.id), tokens=200, cost_usd=Decimal("0"))
        assert _patched_send_message.call_count == 1
        kwargs = _patched_send_message.call_args.kwargs
        assert kwargs["chat_id"] == "manager-chat-42"
        assert "80%" in kwargs["text"]

    async def test_subsequent_calls_do_not_re_alert_80(
        self,
        tenant_with_manager: Tenant,
        _patched_send_message,
    ) -> None:
        await record_usage(str(tenant_with_manager.id), tokens=8_100, cost_usd=Decimal("0"))
        assert _patched_send_message.call_count == 1
        # Stay between 80% and 100% — more calls must NOT re-alert.
        await record_usage(str(tenant_with_manager.id), tokens=500, cost_usd=Decimal("0"))
        await record_usage(str(tenant_with_manager.id), tokens=500, cost_usd=Decimal("0"))
        assert _patched_send_message.call_count == 1

    async def test_cross_100_fires_one_alert(
        self,
        tenant_with_manager: Tenant,
        _patched_send_message,
    ) -> None:
        # Jump straight from 0 → over cap (single record_usage). Both 80%
        # and 100% boundaries cross in one call → both alerts fire.
        await record_usage(str(tenant_with_manager.id), tokens=11_000, cost_usd=Decimal("0"))
        assert _patched_send_message.call_count == 2

        # A subsequent call must NOT re-alert either.
        await record_usage(str(tenant_with_manager.id), tokens=100, cost_usd=Decimal("0"))
        assert _patched_send_message.call_count == 2

    async def test_empty_manager_chat_id_logs_and_skips(
        self,
        tenant: Tenant,
        _patched_send_message,
    ) -> None:
        # `tenant` fixture has no manager_chat_id. Cap is still
        # enforced; the manager just doesn't get pinged.
        await record_usage(str(tenant.id), tokens=11_000, cost_usd=Decimal("0"))
        _patched_send_message.assert_not_called()
        with pytest.raises(TenantQuotaExceeded):
            await enforce_caps(str(tenant.id))

    async def test_cost_threshold_crosses_independent_of_tokens(
        self,
        tenant_with_manager: Tenant,
        _patched_send_message,
    ) -> None:
        # Low token usage but cost > 80% of $5 cap.
        await record_usage(str(tenant_with_manager.id), tokens=10, cost_usd=Decimal("4.50"))
        assert _patched_send_message.call_count == 1


# ---------------------------------------------------------------------------
# UTC midnight rollover
# ---------------------------------------------------------------------------


class TestDayRollover:
    async def test_new_day_resets_view(self, tenant: Tenant) -> None:
        with freeze_time("2026-05-18 23:30:00", tz_offset=0):
            await record_usage(str(tenant.id), tokens=5_000, cost_usd=Decimal("2.50"))
            usage = await get_current_usage(str(tenant.id))
            assert usage.tokens_used == 5_000

        with freeze_time("2026-05-19 00:30:00", tz_offset=0):
            usage = await get_current_usage(str(tenant.id))
            assert usage.tokens_used == 0
            assert usage.cost_used_usd == Decimal("0")


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------


class TestMultiTenantIsolation:
    async def test_tenants_do_not_pollute_each_other(self) -> None:
        a = await _create_tenant_async(name="iso-a")
        b = await _create_tenant_async(name="iso-b")

        await record_usage(str(a.id), tokens=10_000, cost_usd=Decimal("4.00"))
        await record_usage(str(b.id), tokens=100, cost_usd=Decimal("0.01"))

        usage_a = await get_current_usage(str(a.id))
        usage_b = await get_current_usage(str(b.id))
        assert usage_a.tokens_used == 10_000
        assert usage_b.tokens_used == 100
        # Tenant A's cap should trip; B's is comfortably below.
        with pytest.raises(TenantQuotaExceeded):
            await enforce_caps(str(a.id))
        await enforce_caps(str(b.id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_tenant_async(*, name: str) -> Tenant:
    from asgiref.sync import sync_to_async

    return await sync_to_async(Tenant.objects.create, thread_sensitive=False)(
        slug=f"iso-{uuid4().hex[:8]}",
        name=name,
        daily_token_cap=10_000,
        daily_cost_cap_usd=Decimal("5.00"),
    )
