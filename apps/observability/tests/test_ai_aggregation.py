"""AI daily metric aggregation tests (Веха 2 / #771).

Covers:
- Aggregation correctness (counts, percentiles, sums)
- Idempotency (re-run same day → same row)
- Booking correlation window (boundaries: 0min / 60min / 60min+1s / negative gap)
- Threshold semantics (≥ / < boundary edges)
- Empty-day handling (`total_requests=0` → `no_data` status, no alerts)
- Alerting integration (page() invocation on breach)
- Per-tenant isolation in beat task
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.observability.ai_metrics import (
    BOOKING_CORRELATION_WINDOW,
    FALLBACK_RATE_THRESHOLD,
    LATENCY_P95_THRESHOLD_MS,
    MEAN_COST_USD_THRESHOLD,
    TASK_SUCCESS_RATE_THRESHOLD,
    _evaluate_thresholds,
    _percentile,
    aggregate_daily_metrics,
)
from apps.observability.models import AIDailyMetricSummary, AIRequestMetric
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="aidm-test", name="AI Daily Metric Test")


@pytest.fixture
def tenant_other():
    return Tenant.objects.create(slug="aidm-other", name="Other Tenant")


@pytest.fixture
def bot_user(tenant):
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="aidm-1",
    )


@pytest.fixture
def conversation(tenant, bot_user):
    return Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
    )


YESTERDAY = date(2026, 5, 25)
YESTERDAY_NOON_UTC = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)


def _metric(
    tenant,
    bot_user=None,
    conversation=None,
    *,
    created_at=YESTERDAY_NOON_UTC,
    latency_total_ms=1000,
    outcome="success",
    fallback_triggered=False,
    intent="book",
    llm_cost_usd: Decimal | None = None,
    llm_tokens_input=100,
    llm_tokens_output=50,
) -> AIRequestMetric:
    """Test helper to create AIRequestMetric with sensible defaults."""
    m = AIRequestMetric.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        conversation=conversation,
        request_id=uuid.uuid4(),
        message_text_length=42,
        latency_total_ms=latency_total_ms,
        outcome=outcome,
        fallback_triggered=fallback_triggered,
        intent_classified=intent,
        llm_provider="openai",
        llm_tokens_input=llm_tokens_input,
        llm_tokens_output=llm_tokens_output,
        llm_cost_usd=llm_cost_usd,
    )
    # Force created_at to controlled value (auto_now_add overrode).
    AIRequestMetric.all_tenants.filter(pk=m.pk).update(created_at=created_at)
    m.refresh_from_db()
    return m


# ─── TestPercentileHelper ───────────────────────────────────────────────


class TestPercentileHelper:
    def test_empty_list_returns_zero(self):
        assert _percentile([], 95) == 0

    def test_single_value(self):
        assert _percentile([100], 95) == 100

    def test_p50_of_sorted_range(self):
        # 10 values → median ~ index 4 (1-indexed pos 5)
        assert _percentile(list(range(1, 11)), 50) in (5, 6)

    def test_p95_of_sorted_range(self):
        # 100 values 1..100 → p95 ≈ 95
        assert _percentile(list(range(1, 101)), 95) == 95


# ─── TestAggregationCorrectness ─────────────────────────────────────────


class TestAggregationCorrectness:
    def test_simple_rollup(self, tenant, bot_user, conversation):
        # 3 successes + 1 fallback + 1 error
        for _ in range(3):
            _metric(tenant, bot_user, conversation, outcome="success", latency_total_ms=500)
        _metric(
            tenant,
            bot_user,
            conversation,
            outcome="fallback",
            fallback_triggered=True,
            latency_total_ms=300,
        )
        _metric(tenant, bot_user, conversation, outcome="error", latency_total_ms=2000)

        summary = aggregate_daily_metrics(YESTERDAY, tenant)

        assert summary.total_requests == 5
        assert summary.success_count == 3
        assert summary.fallback_count == 1
        assert summary.error_count == 1
        assert summary.escalated_count == 0
        assert summary.fallback_rate == pytest.approx(0.2)

    def test_latency_percentiles(self, tenant, bot_user, conversation):
        latencies = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
        for lat in latencies:
            _metric(tenant, bot_user, conversation, latency_total_ms=lat)

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.total_requests == 10
        # p50 ≈ 500, p95 ≈ 1000 (nearest-rank on 10 values)
        assert 400 <= summary.latency_p50_ms <= 600
        assert summary.latency_p95_ms in (900, 1000)

    def test_cost_aggregation(self, tenant, bot_user, conversation):
        for cost in [Decimal("0.005"), Decimal("0.010"), Decimal("0.015")]:
            _metric(tenant, bot_user, conversation, llm_cost_usd=cost)

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.total_cost_usd == Decimal("0.030000")
        assert summary.mean_cost_usd == Decimal("0.010000")
        # Note: mean_cost == 0.01 → boundary breach (< 0.01 → ok)
        ts = summary.threshold_status["mean_cost_usd"]
        assert ts["status"] == AIDailyMetricSummary.THRESHOLD_BREACH

    def test_intent_distribution(self, tenant, bot_user, conversation):
        _metric(tenant, bot_user, conversation, intent="book")
        _metric(tenant, bot_user, conversation, intent="book")
        _metric(tenant, bot_user, conversation, intent="ask_master")
        _metric(tenant, bot_user, conversation, intent="")  # empty → "(none)"

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.intent_distribution["book"] == 2
        assert summary.intent_distribution["ask_master"] == 1
        assert summary.intent_distribution["(none)"] == 1


# ─── TestIdempotency ────────────────────────────────────────────────────


class TestIdempotency:
    def test_rerun_same_day_no_duplicate_row(self, tenant, bot_user, conversation):
        _metric(tenant, bot_user, conversation)
        first = aggregate_daily_metrics(YESTERDAY, tenant)
        second = aggregate_daily_metrics(YESTERDAY, tenant)
        assert first.pk == second.pk
        assert (
            AIDailyMetricSummary.objects.filter(tenant=tenant, snapshot_date=YESTERDAY).count() == 1
        )

    def test_late_arriving_metric_picked_up_on_rerun(self, tenant, bot_user, conversation):
        _metric(tenant, bot_user, conversation)
        first = aggregate_daily_metrics(YESTERDAY, tenant)
        assert first.total_requests == 1

        # Add a back-fill metric for the same day
        _metric(tenant, bot_user, conversation)
        second = aggregate_daily_metrics(YESTERDAY, tenant)
        assert second.total_requests == 2
        assert first.pk == second.pk  # same row, updated


# ─── TestBookingCorrelation ─────────────────────────────────────────────


class TestBookingCorrelation:
    def test_booking_within_60min_correlates(self, tenant, bot_user, conversation):
        # AI message at noon, booking 30min later
        _metric(tenant, bot_user, conversation, outcome="success")
        conversation.last_booking_at = YESTERDAY_NOON_UTC + timedelta(minutes=30)
        conversation.save()

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.task_success_rate == pytest.approx(1.0)

        m = AIRequestMetric.all_tenants.get(tenant=tenant)
        assert m.success_correlated_at is not None
        assert m.booking_event_id == conversation.id

    def test_booking_at_exactly_60min_correlates(self, tenant, bot_user, conversation):
        """Boundary: gap == 60min → still correlates (≤ inclusive)."""
        _metric(tenant, bot_user, conversation, outcome="success")
        conversation.last_booking_at = YESTERDAY_NOON_UTC + BOOKING_CORRELATION_WINDOW
        conversation.save()

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.task_success_rate == pytest.approx(1.0)

    def test_booking_at_61min_does_not_correlate(self, tenant, bot_user, conversation):
        """Boundary: gap > 60min → no correlation."""
        _metric(tenant, bot_user, conversation, outcome="success")
        conversation.last_booking_at = (
            YESTERDAY_NOON_UTC + BOOKING_CORRELATION_WINDOW + timedelta(seconds=1)
        )
        conversation.save()

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.task_success_rate == 0.0

    def test_booking_before_ai_message_does_not_correlate(self, tenant, bot_user, conversation):
        """Negative gap (booking happened BEFORE AI message) doesn't count."""
        _metric(tenant, bot_user, conversation, outcome="success")
        conversation.last_booking_at = YESTERDAY_NOON_UTC - timedelta(minutes=10)
        conversation.save()

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.task_success_rate == 0.0

    def test_no_booking_no_correlation(self, tenant, bot_user, conversation):
        _metric(tenant, bot_user, conversation, outcome="success")
        # conversation.last_booking_at stays None
        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.task_success_rate == 0.0

    def test_non_success_outcome_skipped_in_correlation(self, tenant, bot_user, conversation):
        """Only `outcome=success` metrics are candidates for correlation."""
        _metric(tenant, bot_user, conversation, outcome="fallback")
        conversation.last_booking_at = YESTERDAY_NOON_UTC + timedelta(minutes=30)
        conversation.save()

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        # fallback rows aren't success candidates
        assert summary.task_success_rate == 0.0


# ─── TestThresholdSemantics ─────────────────────────────────────────────


class TestThresholdSemantics:
    """Adversarial focus Веха 4 — boundary semantics explicit."""

    def test_task_success_rate_exact_80_is_ok(self):
        result = _evaluate_thresholds(
            total_requests=10,
            task_success_rate=TASK_SUCCESS_RATE_THRESHOLD,
            latency_p95_ms=100,
            fallback_rate=0.0,
            mean_cost_usd=Decimal("0.001"),
        )
        # ≥ 0.80 inclusive → exactly 0.80 = ok
        assert result["task_success_rate"]["status"] == "ok"

    def test_task_success_rate_below_80_breach(self):
        result = _evaluate_thresholds(
            total_requests=10,
            task_success_rate=0.79,
            latency_p95_ms=100,
            fallback_rate=0.0,
            mean_cost_usd=Decimal("0.001"),
        )
        assert result["task_success_rate"]["status"] == "breach"

    def test_latency_exact_3000_breach(self):
        result = _evaluate_thresholds(
            total_requests=10,
            task_success_rate=1.0,
            latency_p95_ms=LATENCY_P95_THRESHOLD_MS,  # = 3000
            fallback_rate=0.0,
            mean_cost_usd=Decimal("0.001"),
        )
        # < 3000 exclusive → exactly 3000 = breach
        assert result["latency_p95_ms"]["status"] == "breach"

    def test_latency_2999_ok(self):
        result = _evaluate_thresholds(
            total_requests=10,
            task_success_rate=1.0,
            latency_p95_ms=2999,
            fallback_rate=0.0,
            mean_cost_usd=Decimal("0.001"),
        )
        assert result["latency_p95_ms"]["status"] == "ok"

    def test_fallback_rate_exact_20pct_breach(self):
        result = _evaluate_thresholds(
            total_requests=10,
            task_success_rate=1.0,
            latency_p95_ms=100,
            fallback_rate=FALLBACK_RATE_THRESHOLD,  # = 0.20
            mean_cost_usd=Decimal("0.001"),
        )
        # < 0.20 exclusive → exactly 0.20 = breach
        assert result["fallback_rate"]["status"] == "breach"

    def test_mean_cost_exact_0_01_breach(self):
        result = _evaluate_thresholds(
            total_requests=10,
            task_success_rate=1.0,
            latency_p95_ms=100,
            fallback_rate=0.0,
            mean_cost_usd=MEAN_COST_USD_THRESHOLD,  # = 0.01
        )
        # < 0.01 exclusive → exactly $0.01 = breach
        assert result["mean_cost_usd"]["status"] == "breach"

    def test_empty_day_yields_no_data_status(self):
        result = _evaluate_thresholds(
            total_requests=0,
            task_success_rate=0.0,
            latency_p95_ms=0,
            fallback_rate=0.0,
            mean_cost_usd=Decimal("0"),
        )
        assert result == {"no_data": True}


# ─── TestEmptyDayHandling ───────────────────────────────────────────────


class TestEmptyDayHandling:
    def test_empty_day_yields_zero_row(self, tenant):
        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.total_requests == 0
        assert summary.threshold_status == {"no_data": True}

    def test_empty_day_does_not_fire_alerts(self, tenant):
        from apps.observability.ai_metrics import _alert_breaches

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        with patch("apps.observability.alerting.page") as mock_page:
            fired = _alert_breaches(summary)
        assert fired == 0
        mock_page.assert_not_called()


# ─── TestAlertingIntegration ────────────────────────────────────────────


class TestAlertingIntegration:
    """Verify alerting hook calls `apps.observability.alerting.page()` on breach."""

    def test_latency_breach_fires_alert(self, tenant, bot_user, conversation):
        from apps.observability.ai_metrics import _alert_breaches

        # 1 request with latency above threshold → p95 = same value = breach
        _metric(tenant, bot_user, conversation, latency_total_ms=5000)

        summary = aggregate_daily_metrics(YESTERDAY, tenant)
        assert summary.threshold_status["latency_p95_ms"]["status"] == "breach"

        with patch("apps.observability.alerting.page", return_value=True) as mock_page:
            fired = _alert_breaches(summary)

        assert fired >= 1
        # Verify at least one call mentioned latency_p95_ms
        assert any("latency_p95_ms" in str(call) for call in mock_page.call_args_list)

    def test_dedup_key_per_metric_tenant_date(self, tenant, bot_user, conversation):
        """Same metric + tenant + day → same dedup_key (prevents alert spam)."""
        from apps.observability.ai_metrics import _alert_breaches

        _metric(tenant, bot_user, conversation, latency_total_ms=5000)
        summary = aggregate_daily_metrics(YESTERDAY, tenant)

        with patch("apps.observability.alerting.page") as mock_page:
            _alert_breaches(summary)

        assert mock_page.called
        for call in mock_page.call_args_list:
            kwargs = call.kwargs
            assert "dedup_key" in kwargs
            assert str(tenant.id) in kwargs["dedup_key"]
            assert str(YESTERDAY) in kwargs["dedup_key"]


# ─── TestPerTenantIsolation ─────────────────────────────────────────────


class TestPerTenantIsolation:
    def test_aggregation_isolates_per_tenant(self, tenant, tenant_other, bot_user, conversation):
        other_bu = BotUser.all_tenants.create(
            tenant=tenant_other, channel="max", channel_user_id="other-1"
        )
        other_conv = Conversation.all_tenants.create(tenant=tenant_other, bot_user=other_bu)
        _metric(tenant, bot_user, conversation, latency_total_ms=500)
        _metric(tenant_other, other_bu, other_conv, latency_total_ms=2000)

        s_a = aggregate_daily_metrics(YESTERDAY, tenant)
        s_b = aggregate_daily_metrics(YESTERDAY, tenant_other)

        assert s_a.total_requests == 1
        assert s_a.latency_p95_ms in (500, 500)  # within bound
        assert s_b.total_requests == 1
        assert s_b.latency_p95_ms in (2000, 2000)
