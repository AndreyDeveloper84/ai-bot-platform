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
    llm_model: str = "",
    llm_pass_index: Optional[int] = None,
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
        llm_model: Vendor-resolved model id (e.g. `"gpt-4o-mini"`). Empty when
            no LLM call. DRF-1211.
        llm_pass_index: 1-based index of the LLM call within one user turn
            (multi-pass concierge, DRF-1266). NULL for single-pass writers.

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
        llm_model=llm_model,
        llm_pass_index=llm_pass_index,
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

from django.db import IntegrityError, transaction  # noqa: E402
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


# ─── Implicit feedback signal detection (Tier-A #3, sequence #6) ────────
#
# Per founder pilot_scope_discipline 2026-05-27. Walk AIRequestMetric
# rows + detect 3 deterministic behavioral signals. Inserts dedup'd
# `ImplicitFeedbackSignal` rows (DB unique constraint on
# `(ai_request_metric, signal_type)` makes re-runs idempotent).
#
# Tunables:
#   - CANCELLATION_DETECTION_WINDOW: how long after a metric к look
#     for a booking.cancelled event (24h per founder verdict).
#   - ABANDONED_TOPIC_THRESHOLD: idle window after metric к flag as
#     abandoned (24h per Tau spec — same as cancellation window for
#     consistency).
#   - REPEAT_INTERACTION_WINDOW: same-skill N+1 lookup window
#     (24h, matches conversation session pattern).

CANCELLATION_DETECTION_WINDOW = timedelta(hours=24)
ABANDONED_TOPIC_THRESHOLD = timedelta(hours=24)
REPEAT_INTERACTION_WINDOW = timedelta(hours=24)


def _idempotent_signal_get_or_create(*, defaults: dict, **lookup) -> tuple[Any, bool]:
    """H3 fix (PR #924 adversarial) — `get_or_create` wrapped in a nested
    savepoint так IntegrityError from a concurrent inserter (parallel
    Celery beat / DAG misconfigure) does NOT poison the surrounding
    ``aggregate_daily_metrics`` ``transaction.atomic()``.

    Django's plain ``Model.objects.get_or_create`` catches IntegrityError
    internally and re-SELECTs, but the catch happens AFTER the outer txn
    is marked aborted in PostgreSQL. Wrapping the call in a nested
    ``transaction.atomic()`` opens a savepoint that can be rolled back
    independently — the outer txn survives, we re-fetch via lookup, and
    the duplicate row is the one the parallel worker just wrote.
    """
    from apps.observability.models import ImplicitFeedbackSignal

    try:
        with transaction.atomic():
            return ImplicitFeedbackSignal.all_tenants.get_or_create(defaults=defaults, **lookup)
    except IntegrityError:
        # Concurrent inserter won the race — re-SELECT and report
        # `created=False` so callers do not double-count.
        existing = ImplicitFeedbackSignal.all_tenants.filter(**lookup).first()
        return existing, False


def _detect_implicit_signals(target_date: _date, tenant) -> dict[str, int]:
    """Walk AIRequestMetric rows + insert ImplicitFeedbackSignal rows.

    Per Tier-A #3 (founder sequence #6, 2026-05-27). Runs inside the
    daily aggregation transaction, BEFORE the count rollup так counts
    pick up newly-inserted signals.

    Returns ``{signal_type: inserted_count}`` для telemetry / tests.

    Idempotent: DB unique constraint on (ai_request_metric, signal_type)
    means re-running the same day no-ops on already-detected signals.
    """
    from apps.observability.models import ImplicitFeedbackSignal

    day_start = _dt.combine(target_date, _time.min, tzinfo=_stdlib_tz.utc)
    day_end = day_start + timedelta(days=1)

    counts: dict[str, int] = {
        ImplicitFeedbackSignal.SIGNAL_CANCELLATION_AFTER_SUGGESTION: 0,
        ImplicitFeedbackSignal.SIGNAL_ABANDONED_TOPIC: 0,
        ImplicitFeedbackSignal.SIGNAL_REPEAT_INTERACTION: 0,
    }

    # Source rows для signal detection. Only consider metrics с linked
    # conversation (signals need conversation context). All outcomes
    # qualify — even error outcomes can lead к abandoned/repeat patterns.
    candidate_metrics = list(
        AIRequestMetric.all_tenants.filter(
            tenant=tenant,
            created_at__gte=day_start,
            created_at__lt=day_end,
            conversation__isnull=False,
        )
        .select_related("conversation")
        .only(
            "id",
            "created_at",
            "skill_selected",
            "bot_user_id",
            "conversation__id",
            "conversation__last_booking_at",
            "conversation__last_message_at",
        )
    )

    now = _django_tz.now()

    # ─── Signal 1: cancellation_after_suggestion ───────────────────────
    # Detection: metric → conversation.last_booking_at within 24h AND
    # booking subsequently cancelled (Conversation.is_active=False OR
    # booking RemoteBookingProxy.status=cancelled within 24h of booking).
    #
    # Pilot heuristic — Conversation is the canonical signal source per
    # ADR-0009 + W4 _correlate_task_success pattern. If conversation
    # was soft-deleted (deleted_at NOT NULL) within 24h of booking,
    # treat as «cancelled after suggestion» — user invoked forget-flow
    # or admin closed the conversation, both indicate dissatisfaction.
    #
    # Phase 1 follow-up: when RemoteBookingProxy.cancelled_at lands
    # как proper signal, extend к use that instead of Conversation
    # soft-delete heuristic.
    from apps.conversations.models import Conversation as ConvModel

    for metric in candidate_metrics:
        conv = metric.conversation
        if conv is None or conv.last_booking_at is None:
            continue
        gap_to_booking = conv.last_booking_at - metric.created_at
        if not (timedelta(0) <= gap_to_booking <= CANCELLATION_DETECTION_WINDOW):
            continue
        # Booking happened in-window. Re-fetch conversation для current
        # deleted_at state (read after correlation step bumps `last_booking_at`).
        fresh = (
            ConvModel.all_tenants.filter(pk=conv.pk).only("deleted_at", "last_booking_at").first()
        )
        if fresh is None:
            continue
        # H2 fix (PR #924 adversarial) — require ``deleted_at IS NOT NULL``
        # as the pilot heuristic. The earlier `is_active=False OR deleted_at`
        # gate fired false positives for any conversation that became
        # inactive (admin close / idle-cleanup sweep / explicit
        # close_conversation), conflating «user cancelled after AI
        # suggestion» with «conversation simply closed for any reason».
        #
        # `Conversation.mark_deleted()` sets BOTH `is_active=False` AND
        # `deleted_at=now()`. Conversely, the idle-cleanup sweep and
        # `close_conversation()` only flip `is_active=False` without
        # touching `deleted_at`. So gating strictly on `deleted_at IS
        # NOT NULL` selects user-initiated soft-delete (the forget-flow
        # signal proxy we want) and ignores ops-side housekeeping.
        #
        # Post-pilot upgrade: switch to `RemoteBookingProxy.cancelled_at`
        # как proper cancellation signal once Gamma's booking event
        # consumer wires it up. Documented в ADR-0011 §9.2.
        if fresh.deleted_at is None:
            continue
        cancellation_at = fresh.deleted_at
        gap_to_cancel = cancellation_at - conv.last_booking_at
        if not (timedelta(0) <= gap_to_cancel <= CANCELLATION_DETECTION_WINDOW):
            continue
        # Tier-A #3 adversarial CR MED-14 (2026-05-31) — ``tenant`` lives
        # in the LOOKUP kwargs (not ``defaults``) so a misconfigured row
        # with the wrong tenant cannot satisfy this get-side and silently
        # claim ``created=False``. The DB unique constraint is on
        # ``(ai_request_metric, signal_type)``; including tenant in lookup
        # makes the SELECT specific so cross-tenant inconsistency surfaces
        # as IntegrityError → savepoint rollback → re-SELECT returns None,
        # which the caller treats as «no-op».
        _, created = _idempotent_signal_get_or_create(
            ai_request_metric_id=metric.id,
            signal_type=ImplicitFeedbackSignal.SIGNAL_CANCELLATION_AFTER_SUGGESTION,
            tenant=tenant,
            defaults={
                "conversation_id": conv.id,
                "payload": {
                    "booked_at": conv.last_booking_at.isoformat(),
                    "cancelled_at": cancellation_at.isoformat(),
                    "gap_to_booking_seconds": int(gap_to_booking.total_seconds()),
                    "gap_to_cancel_seconds": int(gap_to_cancel.total_seconds()),
                },
            },
        )
        if created:
            counts[ImplicitFeedbackSignal.SIGNAL_CANCELLATION_AFTER_SUGGESTION] += 1

    # ─── Signal 2: abandoned_topic ───────────────────────────────────
    # Detection: metric.created_at + 24h has passed AND no booking AND
    # NO user-side `Message` row exists with created_at > metric.created_at.
    #
    # H1 fix (PR #924 adversarial) — the previous implementation relied
    # on `Conversation.last_message_at`, but the orchestrator pipeline
    # path (`apps/orchestrator/pipeline.py::_save_user_message` /
    # `_save_assistant`) writes Message rows directly с `.objects.create()`
    # and never bumps `Conversation.last_message_at`. That field is only
    # maintained by `apps/conversations/services.py::record_message`,
    # which the orchestrator does NOT call. Result: every pipeline-served
    # conversation had `last_message_at=None` indefinitely, so the
    # «engagement happened after metric» short-circuit could never fire
    # — every metric for a booking-less conversation got flagged abandoned.
    #
    # Now: query `Message.all_tenants.filter(conversation=conv,
    # created_at__gt=metric.created_at, role="user").exists()`. This
    # reads ground-truth from the canonical Message rows, regardless of
    # which writer path put them there. `role="user"` scopes к user-side
    # engagement only — assistant follow-ups cannot count as engagement
    # by themselves.
    from apps.conversations.models import Message  # noqa: PLC0415 — late import avoids cycle at module load

    for metric in candidate_metrics:
        conv = metric.conversation
        if conv is None:
            continue
        if conv.last_booking_at is not None:
            # Booking exists → topic не abandoned (may be cancellation
            # signal, handled above).
            continue
        if (now - metric.created_at) < ABANDONED_TOPIC_THRESHOLD:
            # Idle window not yet elapsed — re-check on next daily run.
            continue
        # H1: ground-truth engagement check against actual Message rows.
        user_engaged_after = Message.all_tenants.filter(
            conversation_id=conv.id,
            created_at__gt=metric.created_at,
            role="user",
        ).exists()
        if user_engaged_after:
            continue
        # MED-14: tenant in LOOKUP (see signal 1 comment above).
        _, created = _idempotent_signal_get_or_create(
            ai_request_metric_id=metric.id,
            signal_type=ImplicitFeedbackSignal.SIGNAL_ABANDONED_TOPIC,
            tenant=tenant,
            defaults={
                "conversation_id": conv.id,
                "payload": {
                    "idle_seconds": int((now - metric.created_at).total_seconds()),
                },
            },
        )
        if created:
            counts[ImplicitFeedbackSignal.SIGNAL_ABANDONED_TOPIC] += 1

    # ─── Signal 3: repeat_interaction ───────────────────────────────
    # Detection: same (bot_user, skill_selected) appears N+1 times
    # within 24h. The 2nd+ occurrence gets the signal — the first one
    # is the «baseline» (we don't know yet it'll repeat).
    #
    # Index lookup: group by (bot_user_id, skill_selected), find rows
    # с >=2 occurrences within 24h, mark all-but-first.
    seen_pairs: dict[tuple[Any, str], list[Any]] = {}
    for metric in candidate_metrics:
        if not metric.skill_selected:
            continue
        if metric.bot_user_id is None:
            continue
        key = (metric.bot_user_id, metric.skill_selected)
        seen_pairs.setdefault(key, []).append(metric)
    for key, metrics_for_key in seen_pairs.items():
        if len(metrics_for_key) < 2:
            continue
        # Sort by created_at ascending — first is baseline, 2nd+ are
        # repeats. Window check: each repeat must be within 24h of
        # the immediately-preceding interaction (chain semantics).
        metrics_sorted = sorted(metrics_for_key, key=lambda m: m.created_at)
        prev = metrics_sorted[0]
        for current in metrics_sorted[1:]:
            if (current.created_at - prev.created_at) > REPEAT_INTERACTION_WINDOW:
                prev = current
                continue
            # MED-14: tenant in LOOKUP (see signal 1 comment above).
            _, created = _idempotent_signal_get_or_create(
                ai_request_metric_id=current.id,
                signal_type=ImplicitFeedbackSignal.SIGNAL_REPEAT_INTERACTION,
                tenant=tenant,
                defaults={
                    "conversation_id": current.conversation_id,
                    "payload": {
                        "skill": current.skill_selected,
                        "prev_metric_id": str(prev.id),
                        "gap_seconds": int((current.created_at - prev.created_at).total_seconds()),
                    },
                },
            )
            if created:
                counts[ImplicitFeedbackSignal.SIGNAL_REPEAT_INTERACTION] += 1
            prev = current

    return counts


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

        # Step 1.5: detect implicit feedback signals (Tier-A #3, founder
        # sequence #6, 2026-05-27). Same scan reuses the metric+
        # conversation join for cancellation/abandoned/repeat detection.
        # Return value is per-signal inserted counts; we don't use it
        # here (summary rollup below re-queries from the DB to capture
        # ALL signals for the day including prior-run rows), but the
        # detector's side-effect (inserting signal rows) is the point.
        _detect_implicit_signals(target_date, tenant)

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

        # Signal counts for the day — read from ImplicitFeedbackSignal
        # filtered by SOURCE METRIC's created_at (NOT signal's
        # recorded_at — re-runs / backfills could detect signals days
        # later, but the signal belongs to the target_date that the
        # source metric landed on). The DB unique constraint makes
        # re-runs safe.
        from apps.observability.models import ImplicitFeedbackSignal

        signal_rollup_qs = ImplicitFeedbackSignal.all_tenants.filter(
            tenant=tenant,
            ai_request_metric__created_at__gte=day_start,
            ai_request_metric__created_at__lt=day_end,
        ).values_list("signal_type", flat=True)
        signal_type_counter: Counter[str] = Counter(signal_rollup_qs)

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
                # Tier-A #3 signal counts (founder sequence #6).
                "cancelled_after_suggestion_count": signal_type_counter.get(
                    ImplicitFeedbackSignal.SIGNAL_CANCELLATION_AFTER_SUGGESTION, 0
                ),
                "abandoned_topic_count": signal_type_counter.get(
                    ImplicitFeedbackSignal.SIGNAL_ABANDONED_TOPIC, 0
                ),
                "repeat_interaction_count": signal_type_counter.get(
                    ImplicitFeedbackSignal.SIGNAL_REPEAT_INTERACTION, 0
                ),
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
