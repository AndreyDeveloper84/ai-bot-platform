"""AI request metric recorder — sanctioned write path for `AIRequestMetric`.

Per AI observability epic #769 / Веха 1 / task #770.

# What this module provides

`record_ai_request(...)` — single function the AI request hot path
calls once per inbound user message after the orchestrator + skill +
LLM finish (success / fallback / error / escalation). One row per
call. Synchronous INSERT — target <5ms overhead on the parent request
which already takes 1-3s end-to-end.

# Caller pattern (W2 wires call sites in parallel PR)

```python
from apps.observability.ai_metrics import record_ai_request

# In apps/orchestrator/pipeline.py / apps/skills/<each>/skill.py:
record_ai_request(
    tenant=tenant,
    bot_user=bot_user,
    conversation=conversation,
    request_id=trace_id,
    message_text_length=len(user_message_text),
    intent_classified=intent_label or "",
    intent_confidence=confidence_or_none,
    skill_selected=skill_name or "",
    fallback_triggered=did_fallback,
    latency_total_ms=elapsed_ms,
    latency_llm_ms=llm_ms,
    latency_skill_ms=skill_ms,
    llm_provider="openai",
    llm_tokens_input=usage.input_tokens,
    llm_tokens_output=usage.output_tokens,
    llm_cost_usd=Decimal("0.001234"),
    outcome="success",
)
```

# Caller contract

- Tenant MUST be in scope when called — uses `.all_tenants.create(...)`
  internally but the recorder's contract is «scoped from caller side»
  to surface accidental cross-tenant emissions to ops attention.
- `record_ai_request` swallows no exceptions — caller chooses retry /
  log strategy. Sync DB failures bubble up as `IntegrityError` /
  `OperationalError`.
- Veha 1 deliberately keeps this simple. If observability emission
  becomes a hot-path concern (P99 spike), Веха 2+ may move to an
  async outbox pattern.

# What this module does NOT do

- Aggregation / thresholds → Веха 2 (#771)
- Dashboard rendering → Веха 3 (#772)
- Booking-event correlation → Веха 2 aggregation job (fills
  `booking_event_id` + `success_correlated_at` retroactively)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.observability.models import AIRequestMetric
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


def record_ai_request(
    *,
    tenant: Tenant,
    bot_user: Optional[BotUser],
    conversation: Optional[Conversation],
    request_id: UUID,
    message_text_length: int,
    latency_total_ms: int,
    outcome: str,
    intent_classified: str = "",
    intent_confidence: Optional[float] = None,
    skill_selected: str = "",
    fallback_triggered: bool = False,
    latency_llm_ms: Optional[int] = None,
    latency_skill_ms: Optional[int] = None,
    llm_provider: str = "",
    llm_tokens_input: Optional[int] = None,
    llm_tokens_output: Optional[int] = None,
    llm_cost_usd: Optional[Decimal] = None,
) -> AIRequestMetric:
    """Record one `AIRequestMetric` row for the just-completed AI request.

    All kwargs are keyword-only — caller must name every argument so the
    instrumentation reads self-documenting at call sites.

    Args:
        tenant: Owning tenant (required). PROTECT FK in the model.
        bot_user: The user whose message triggered the request. NULL allowed
            for system-triggered AI calls (proactive nudges).
        conversation: Owning conversation. NULL allowed for ad-hoc / out-of-band.
        request_id: Trace correlation UUID — match `trace_id_scope()` value.
        message_text_length: Inbound text length in chars (not the body).
        latency_total_ms: End-to-end wall-clock duration in ms.
        outcome: One of `AIRequestMetric.OUTCOME_*` — `success` / `error` /
            `fallback` / `escalated`.
        intent_classified: Intent label produced by the classifier. Empty
            when no classifier ran (early fallback / error before dispatch).
        intent_confidence: 0..1 classifier confidence. NULL when no classifier.
        skill_selected: Skill registry name (e.g. `"booking"`). Empty on early fallback.
        fallback_triggered: True when dispatcher chose clarification over execution.
        latency_llm_ms: Subset of total — LLM call duration. NULL when no LLM.
        latency_skill_ms: Subset of total — skill execute() duration. NULL on early exit.
        llm_provider: Provider slug — `"openai"` / `"anthropic"` / `"yandex"`. Empty when no LLM.
        llm_tokens_input: Input tokens billed. NULL when no LLM.
        llm_tokens_output: Output tokens billed. NULL when no LLM.
        llm_cost_usd: USD cost for the LLM call (6 dp precision). NULL when no LLM.

    Returns:
        The persisted `AIRequestMetric` row.

    Raises:
        ValueError: `outcome` not in `OUTCOME_CHOICES`.
        django.db.IntegrityError: schema-level conflict (FK violation, etc.)
            — bubbles up to caller.
    """
    valid_outcomes = {choice[0] for choice in AIRequestMetric.OUTCOME_CHOICES}
    if outcome not in valid_outcomes:
        raise ValueError(
            f"record_ai_request: outcome={outcome!r} not in "
            f"AIRequestMetric.OUTCOME_CHOICES ({sorted(valid_outcomes)})."
        )

    return AIRequestMetric.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        conversation=conversation,
        request_id=request_id,
        message_text_length=message_text_length,
        intent_classified=intent_classified,
        intent_confidence=intent_confidence,
        skill_selected=skill_selected,
        fallback_triggered=fallback_triggered,
        latency_total_ms=latency_total_ms,
        latency_llm_ms=latency_llm_ms,
        latency_skill_ms=latency_skill_ms,
        llm_provider=llm_provider,
        llm_tokens_input=llm_tokens_input,
        llm_tokens_output=llm_tokens_output,
        llm_cost_usd=llm_cost_usd,
        outcome=outcome,
    )


# ─── Daily aggregation (Веха 2 / #771) ──────────────────────────────────


from collections import Counter  # noqa: E402
from datetime import (  # noqa: E402
    date as _date,
    datetime as _dt,
    time as _time,
    timedelta,
    timezone as _stdlib_tz,
)

from django.db import transaction  # noqa: E402
from django.utils import timezone as _django_tz  # noqa: E402

from apps.observability.models import AIDailyMetricSummary  # noqa: E402

# Pilot thresholds per memory `project_ai_concierge_doc_extracts` Tier-A #1.
# Adversarial focus Веха 2: boundary semantics — explicit ≥ / < per metric.
TASK_SUCCESS_RATE_THRESHOLD = 0.80  # ≥ 0.80 → ok
LATENCY_P95_THRESHOLD_MS = 3000  # < 3000ms → ok
FALLBACK_RATE_THRESHOLD = 0.20  # < 0.20 → ok
MEAN_COST_USD_THRESHOLD = Decimal("0.01")  # < $0.01 → ok

# Booking correlation lookback window (heuristic).
BOOKING_CORRELATION_WINDOW = timedelta(minutes=60)


def _percentile(sorted_values: list[int], pct: float) -> int:
    """Nearest-rank percentile on a pre-sorted list. Returns 0 on empty input.

    Round-1 adversarial F-5 fix: use `math.ceil` to match the docstring's
    «1-indexed position = ceil(p/100 * N)» contract. Previous code used
    `int(round(...))` which is Python's banker's rounding, producing
    off-by-one drift vs numpy/pandas conventions on edge sizes.
    """
    if not sorted_values:
        return 0
    import math

    n = len(sorted_values)
    k = max(0, min(n - 1, math.ceil(pct / 100.0 * n) - 1))
    return sorted_values[k]


def _correlate_task_success(target_date: _date, tenant) -> int:
    """Walk uncorrelated success metrics + match to Conversation.last_booking_at.

    Per Q1 verdict 2026-05-26: Conversation.last_booking_at is the
    canonical correlation source (populated by Gamma's #442 booking
    consumer from `booking.confirmed` events per event-contract.md §3.1.4).

    Match rule: `metric.created_at <= conv.last_booking_at
    <= metric.created_at + 60min` → metric is a «task success»
    (heuristic per Q4 verdict 2026-05-25).

    Returns count of newly-correlated rows for the day.
    """
    # Date-range filter on created_at — naive comparison against UTC date.
    day_start = _dt.combine(target_date, _time.min, tzinfo=_stdlib_tz.utc)
    day_end = day_start + timedelta(days=1)

    candidate_metrics = (
        AIRequestMetric.all_tenants.filter(
            tenant=tenant,
            created_at__gte=day_start,
            created_at__lt=day_end,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            success_correlated_at__isnull=True,
            conversation__isnull=False,
        )
        .select_related("conversation")
        .only(
            "id",
            "created_at",
            "conversation__id",
            "conversation__last_booking_at",
        )
    )

    correlated = 0
    now = _django_tz.now()
    for metric in candidate_metrics:
        booking_at = metric.conversation.last_booking_at  # type: ignore[union-attr]
        if booking_at is None:
            continue
        gap = booking_at - metric.created_at
        if timedelta(0) <= gap <= BOOKING_CORRELATION_WINDOW:
            AIRequestMetric.all_tenants.filter(pk=metric.pk).update(
                success_correlated_at=now,
                # Conversation.id used as correlation reference until Ayla
                # exposes a stable booking event UUID via event consumer.
                # When booking.confirmed event store wires up, switch to
                # the canonical event id.
                booking_event_id=metric.conversation_id,  # type: ignore[arg-type]
            )
            correlated += 1
    return correlated


def _evaluate_thresholds(
    *,
    total_requests: int,
    task_success_rate: float,
    latency_p95_ms: int,
    fallback_rate: float,
    mean_cost_usd: Decimal,
) -> dict[str, Any]:
    """Per-metric threshold verdict for dashboard + alerting.

    Boundary semantics (adversarial focus Веха 4):
      - task_success_rate ≥ 0.80 → ok  (≥ inclusive on the success side)
      - latency_p95_ms      < 3000 → ok  (< exclusive on the latency side)
      - fallback_rate       < 0.20 → ok  (< exclusive — exactly 20% = breach)
      - mean_cost_usd       < 0.01 → ok  (< exclusive — exactly $0.01 = breach)

    Empty-day handling: `total_requests == 0` → `{"no_data": True}` —
    no alerts fire, dashboard renders «no traffic» badge.
    """
    if total_requests == 0:
        return {"no_data": True}

    return {
        "task_success_rate": {
            "value": task_success_rate,
            "threshold": TASK_SUCCESS_RATE_THRESHOLD,
            "op": ">=",
            "status": (
                AIDailyMetricSummary.THRESHOLD_OK
                if task_success_rate >= TASK_SUCCESS_RATE_THRESHOLD
                else AIDailyMetricSummary.THRESHOLD_BREACH
            ),
        },
        "latency_p95_ms": {
            "value": latency_p95_ms,
            "threshold": LATENCY_P95_THRESHOLD_MS,
            "op": "<",
            "status": (
                AIDailyMetricSummary.THRESHOLD_OK
                if latency_p95_ms < LATENCY_P95_THRESHOLD_MS
                else AIDailyMetricSummary.THRESHOLD_BREACH
            ),
        },
        "fallback_rate": {
            "value": fallback_rate,
            "threshold": FALLBACK_RATE_THRESHOLD,
            "op": "<",
            "status": (
                AIDailyMetricSummary.THRESHOLD_OK
                if fallback_rate < FALLBACK_RATE_THRESHOLD
                else AIDailyMetricSummary.THRESHOLD_BREACH
            ),
        },
        "mean_cost_usd": {
            "value": float(mean_cost_usd),
            "threshold": float(MEAN_COST_USD_THRESHOLD),
            "op": "<",
            "status": (
                AIDailyMetricSummary.THRESHOLD_OK
                if mean_cost_usd < MEAN_COST_USD_THRESHOLD
                else AIDailyMetricSummary.THRESHOLD_BREACH
            ),
        },
    }


def aggregate_daily_metrics(target_date: _date, tenant) -> AIDailyMetricSummary:
    """Compute one `AIDailyMetricSummary` row for `(tenant, target_date)`.

    Steps (single transaction):
      1. Correlate uncorrelated success metrics with `Conversation.last_booking_at`
         (back-fills `success_correlated_at` + `booking_event_id` on matching rows).
      2. Aggregate counts, percentiles, sums for the day.
      3. Evaluate the 4 pilot thresholds → JSONB verdict.
      4. `update_or_create` keyed on `(tenant, snapshot_date)` — idempotent.

    Returns the persisted summary row. Caller decides whether to alert
    (the Celery beat task fires `page()` on breaches).
    """
    day_start = _dt.combine(target_date, _time.min, tzinfo=_stdlib_tz.utc)
    day_end = day_start + timedelta(days=1)

    with transaction.atomic():
        # Step 1: correlate before counting.
        _correlate_task_success(target_date, tenant)

        # Step 2: aggregate counts + percentiles.
        rows = list(
            AIRequestMetric.all_tenants.filter(
                tenant=tenant,
                created_at__gte=day_start,
                created_at__lt=day_end,
            ).values(
                "outcome",
                "intent_classified",
                "latency_total_ms",
                "llm_tokens_input",
                "llm_tokens_output",
                "llm_cost_usd",
                "fallback_triggered",
                "success_correlated_at",
            )
        )

        total = len(rows)
        outcome_counter: Counter[str] = Counter(r["outcome"] for r in rows)
        intent_counter: Counter[str] = Counter((r["intent_classified"] or "(none)") for r in rows)
        fallback_count = sum(1 for r in rows if r["fallback_triggered"])
        correlated_count = sum(1 for r in rows if r["success_correlated_at"] is not None)

        latencies = sorted(r["latency_total_ms"] for r in rows)
        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)

        tokens_in = sum(r["llm_tokens_input"] or 0 for r in rows)
        tokens_out = sum(r["llm_tokens_output"] or 0 for r in rows)
        total_cost: Decimal = sum((r["llm_cost_usd"] or Decimal("0") for r in rows), Decimal("0"))
        mean_cost: Decimal = (total_cost / total) if total else Decimal("0")

        success_rate = (correlated_count / total) if total else 0.0
        fb_rate = (fallback_count / total) if total else 0.0

        threshold_status = _evaluate_thresholds(
            total_requests=total,
            task_success_rate=success_rate,
            latency_p95_ms=p95,
            fallback_rate=fb_rate,
            mean_cost_usd=mean_cost,
        )

        summary, _ = AIDailyMetricSummary.objects.update_or_create(
            tenant=tenant,
            snapshot_date=target_date,
            defaults={
                "total_requests": total,
                "success_count": outcome_counter.get(AIRequestMetric.OUTCOME_SUCCESS, 0),
                "error_count": outcome_counter.get(AIRequestMetric.OUTCOME_ERROR, 0),
                "fallback_count": fallback_count,
                "escalated_count": outcome_counter.get(AIRequestMetric.OUTCOME_ESCALATED, 0),
                "latency_p50_ms": p50,
                "latency_p95_ms": p95,
                "latency_p99_ms": p99,
                "total_tokens_input": tokens_in,
                "total_tokens_output": tokens_out,
                "total_cost_usd": total_cost,
                "mean_cost_usd": mean_cost,
                "task_success_rate": success_rate,
                "fallback_rate": fb_rate,
                "intent_distribution": dict(intent_counter),
                "threshold_status": threshold_status,
            },
        )

    return summary


def _alert_breaches(summary: AIDailyMetricSummary) -> int:
    """Fire alerts for any threshold breach in this summary. Returns alert count.

    Reuses `apps.observability.alerting.page()` per Q2 verdict 2026-05-26.
    Severity = `warning` for single-day breaches; rolling-pattern alerts
    are post-pilot work.

    Empty-day summaries (`no_data`) skip — no alert noise on quiet days.
    """
    from apps.observability.alerting import page

    if summary.threshold_status.get("no_data"):
        return 0

    fired = 0
    for metric_name, status in summary.threshold_status.items():
        if not isinstance(status, dict):
            continue
        if status.get("status") != AIDailyMetricSummary.THRESHOLD_BREACH:
            continue
        title = (
            f"AI quality threshold breach: {metric_name} "
            f"tenant={summary.tenant.slug} date={summary.snapshot_date}"
        )
        body = (
            f"Metric: {metric_name}\n"
            f"Observed: {status['value']}\n"
            f"Threshold: {status['op']} {status['threshold']}\n"
            f"Status: {status['status']}\n"
            f"\nTotal requests today: {summary.total_requests}\n"
            f"Tenant: {summary.tenant.slug}\n"
            f"Date: {summary.snapshot_date}"
        )
        dedup_key = f"ai_quality:{summary.tenant_id}:{summary.snapshot_date}:{metric_name}"
        if page("warning", title, body, dedup_key=dedup_key):
            fired += 1
    return fired
