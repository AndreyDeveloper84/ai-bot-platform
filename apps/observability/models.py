"""Observability persistence models.

Models:

  - :class:`ShadowDeltaSnapshot` (Sprint 8 / S3 / DRF-718) — per-day
    per-tenant agreement metrics from shadow-mode A/B comparison.
    Dashboard (D1) + strict-scope flip gate (F1) read history.

  - :class:`AIRequestMetric` (Веха 1 of AI observability epic #769) —
    per-AI-request metric row capturing intent / skill / latency /
    tokens / cost / outcome. Source data for daily aggregation
    (Веха 2 → :class:`AIDailyMetricSummary`) and dashboard
    (Веха 3 → admin view).

  - :class:`ImplicitFeedbackSignal` (Tier-A #3, founder
    pilot_scope_discipline sequence #6, 2026-05-27) — per-event
    behavioral signal captured при daily aggregation walk. 3 pilot
    signals: cancellation_after_suggestion, abandoned_topic,
    repeat_interaction. NLU-requiring signals (skipped_suggestion,
    pursued_alternative) deferred к Phase 1.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class ShadowDeltaSnapshot(models.Model):
    """Persisted result of one ``compute_daily_delta(date, tenant)`` run.

    Acceptance gate readouts read from this table, so its uniqueness
    contract is load-bearing: at most one row per ``(tenant, date)``.
    The S4 Celery beat is idempotent by `update_or_create` keyed on
    those two fields.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="shadow_delta_snapshots",
        help_text="Owning tenant. PROTECT — a dropped tenant's history is "
        "forensic data + a billing/legal incident path.",
    )
    snapshot_date = models.DateField(
        db_index=True,
        help_text="The UTC date the delta was computed for. The S4 task "
        "schedules at 08:00 МСК and reads yesterday's shadow rows.",
    )

    # Top-level agreement floats — duplicated from `payload` for index
    # access. Lets the dashboard sort by agreement without unpacking the
    # JSON payload on every row.
    intent_agreement = models.FloatField(
        default=0.0,
        help_text="0..1. % of shadow Message rows whose intent matched mysite ground truth.",
    )
    action_type_agreement = models.FloatField(
        default=0.0,
        help_text="0..1. % match on action_type. Captures handoff drift.",
    )
    sample_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of (shadow, ground-truth) pairs that matched "
        "the join keys. Zero = missing CSV or no shadow traffic.",
    )

    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full DeltaSummary serialisation. Includes latency p50/p95 "
        "deltas, error_delta_pct, and any debug fields S5 needs.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # Tenancy system-check opt-out (tenancy.W900 / W901).
    # ShadowDeltaSnapshot is an operator observability artefact — the
    # admin dashboard at apps/observability/views.py reads cross-tenant
    # via ``.filter(snapshot_date__gte=cutoff)`` (no tenant filter,
    # intentional), and the S4 Celery beat iterates tenants explicitly
    # in ``compute_daily_delta(date, tenant)``. Auto-scoping by
    # current_tenant() would break the dashboard (which has no tenant
    # in scope) and silently return empty. Documented intentional
    # deviation; explicit-tenant pattern in the beat task is the
    # load-bearing contract.
    _IGNORE_TENANT_MANAGER_CHECK = True

    class Meta:
        verbose_name = "Shadow delta snapshot"
        verbose_name_plural = "Shadow delta snapshots"
        ordering = ["-snapshot_date", "tenant"]
        constraints = [
            # S4 (DRF-719) wraps writes in update_or_create keyed on
            # (tenant, snapshot_date). Without this constraint a rerun
            # of the daily task would silently double-count.
            models.UniqueConstraint(
                fields=["tenant", "snapshot_date"],
                name="shadow_delta_one_per_tenant_per_day",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "-snapshot_date"]),
        ]

    def __str__(self) -> str:
        return f"ShadowDeltaSnapshot[{self.tenant_id} @ {self.snapshot_date}]"


class AIRequestMetric(models.Model):
    """Per-AI-request observability row (AI observability epic #769 / Веха 1).

    One row per AI-handled inbound request (skill dispatch + LLM call +
    fallback / clarification path). Records the input characteristics,
    routing decision, performance, LLM economics, and outcome — enough
    to compute the 5 pilot thresholds per memory
    `project_ai_concierge_doc_extracts` Tier-A #1:

      - Task Success Rate (heuristic) ≥ 80%
      - Response Latency p95 < 3000ms
      - Fallback Rate < 20%
      - Cost per Request < $0.01
      - Intent Accuracy ≥ 80% — DEFERRED post-pilot (#773)

    Schema spec per tech-lead handoff 2026-05-26. Writes happen
    synchronously from the AI request hot path via
    `apps.observability.ai_metrics.record_ai_request()` — target <5ms
    overhead on a request that already takes 1-3s end-to-end.

    Tenant-scoped via :class:`TenantScopedManager` — standard pattern
    consistent with `BotUser` / `TenantStaff` / `CatalogMaster`. The
    `all_tenants` escape hatch exists for the daily-aggregation Celery
    task which iterates tenants explicitly (Веха 2).

    # W2 emission points (separate PR, not Веха 1 scope)

    W2 stream owns the emission sites:
      - `apps/orchestrator/` — intent classification + skill routing
      - `apps/skills/<each>/` — post-execute() outcome recording
      - `apps/llm/` client wrapper — token + cost capture

    Веха 1 ships the schema + recorder API; W2 wires the call sites in
    a parallel PR keyed on the documented kwargs of `record_ai_request()`.

    # Task success correlation (Веха 2 — NOT Веха 1)

    `booking_event_id` + `success_correlated_at` are populated by the
    daily aggregation job's heuristic: when a booking event lands
    within 60 min of the last AI message in the same conversation, the
    job back-fills these fields on the originating `AIRequestMetric`
    rows. Веха 1 leaves these NULL.
    """

    OUTCOME_SUCCESS = "success"
    OUTCOME_ERROR = "error"
    OUTCOME_FALLBACK = "fallback"
    OUTCOME_ESCALATED = "escalated"
    OUTCOME_CHOICES = [
        (OUTCOME_SUCCESS, "Success — AI handled the request end-to-end"),
        (OUTCOME_ERROR, "Error — exception during skill / LLM execution"),
        (OUTCOME_FALLBACK, "Fallback — confidence < threshold, asked clarification"),
        (OUTCOME_ESCALATED, "Escalated — handed off to human operator"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When the AI request finished. Drives the daily aggregation "
        "cursor (Веха 2) — rollup pulls rows for `date(created_at) == target_date`.",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="ai_request_metrics",
        help_text="Owning tenant. PROTECT — metric history is forensic + "
        "billing-adjacent data; dropping a tenant must not silently nuke it.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.PROTECT,
        related_name="ai_request_metrics",
        null=True,
        blank=True,
        help_text="The user whose message triggered the AI request. PROTECT "
        "to preserve metric history; nullable for system-triggered AI calls "
        "(e.g. proactive nudges, scheduled retention messages).",
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        related_name="ai_request_metrics",
        null=True,
        blank=True,
        help_text="Owning conversation. SET_NULL — Conversation soft-deletes "
        "are common (152-ФЗ forget-flow); metric row survives for billing/audit.",
    )
    request_id = models.UUIDField(
        db_index=True,
        help_text="Trace correlation ID — matches the orchestrator's trace_id_scope() "
        "so a single user turn can be reconstructed across logs + Event rows + "
        "this metric. Indexed for «show me everything for trace X» queries.",
    )

    # ─── Intent + skill ──────────────────────────────────────────────────
    message_text_length = models.IntegerField(
        help_text="Length of the inbound user message text in characters. "
        "Stored separately from message body (which lives in Conversation "
        "+ Message rows) so cost-vs-length analysis doesn't require joining.",
    )
    intent_classified = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="The intent label the classifier produced (e.g. 'book_service', "
        "'ask_master_availability'). Empty when no classifier ran (e.g. early "
        "fallback). Indexed for per-intent aggregation in Веха 2.",
    )
    intent_confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="0..1 classifier confidence. NULL when no classifier ran. "
        "Drives the fallback-trigger threshold (BeautyGo Tier-A #4 — confidence < 0.7).",
    )
    skill_selected = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="The skill registry name that handled the request (e.g. "
        "'booking', 'faq', 'echo'). Empty on early fallback / error before dispatch.",
    )
    fallback_triggered = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True when the skill dispatcher chose clarification over execution "
        "(confidence below threshold OR no skill matched). Drives the Fallback Rate "
        "threshold metric (< 20% target).",
    )

    # ─── Performance ─────────────────────────────────────────────────────
    latency_total_ms = models.IntegerField(
        help_text="Total wall-clock time from request receipt to outbound dispatch, "
        "in milliseconds. Drives the Latency p95 < 3000ms threshold.",
    )
    latency_llm_ms = models.IntegerField(
        null=True,
        blank=True,
        help_text="LLM API call duration in ms (subset of latency_total). NULL when "
        "no LLM call (cached / non-LLM skill path).",
    )
    latency_skill_ms = models.IntegerField(
        null=True,
        blank=True,
        help_text="Skill execute() duration in ms (subset of latency_total). NULL on "
        "early fallback / error before skill dispatch.",
    )

    # ─── LLM economics ───────────────────────────────────────────────────
    llm_provider = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="LLM provider slug — 'openai', 'anthropic', 'yandex', etc. "
        "Empty when no LLM call. Used for per-provider cost breakdown.",
    )
    llm_tokens_input = models.IntegerField(
        null=True,
        blank=True,
        help_text="Input tokens billed by the provider. NULL when no LLM call.",
    )
    llm_tokens_output = models.IntegerField(
        null=True,
        blank=True,
        help_text="Output tokens billed by the provider. NULL when no LLM call.",
    )
    llm_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="USD cost for this single LLM call (6 dp = 0.000001 = a fraction of "
        "a cent). Drives the Cost per Request < $0.01 threshold. NULL when no LLM call.",
    )
    llm_model = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Vendor-resolved model id for this LLM call (e.g. 'gpt-4o-mini', "
        "'claude-haiku-4-5'). Empty when no LLM call or the writer predates "
        "DRF-1211 (pipeline / shadow paths). Needed to attribute cost to a "
        "model when SKILL_LLM_PROVIDER reroutes a skill.",
    )
    llm_pass_index = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="1-based index of the LLM call within one user turn "
        "(multi-pass concierge, DRF-1266: 1 = first call, 2+ = follow-up "
        "passes fed with the tool result). NULL for single-pass writers "
        "(pipeline, shadow) — separates the cost of multi-pass from "
        "general traffic growth.",
    )

    # ─── Outcome ─────────────────────────────────────────────────────────
    outcome = models.CharField(
        max_length=16,
        choices=OUTCOME_CHOICES,
        db_index=True,
        help_text="Terminal state of the AI handling: success / error / fallback / "
        "escalated. Drives the Task Success Rate heuristic via correlation with "
        "downstream booking events (filled by Веха 2 aggregation job).",
    )

    # ─── Task success correlation (Веха 2 fills) ────────────────────────
    booking_event_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="If a booking event landed within 60 min of this AI message in the "
        "same conversation, the daily aggregation job (Веха 2) records the booking "
        "event's id here. Drives Task Success Rate (heuristic). NULL until correlated.",
    )
    success_correlated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the aggregation job linked this row to a booking event. "
        "NULL until correlated. Lets late-arriving aggregation distinguish "
        "«not yet checked» from «checked, no correlation found».",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "AI request metric"
        verbose_name_plural = "AI request metrics"
        ordering = ["-created_at"]
        indexes = [
            # Primary aggregation lookup — «rows for tenant X in date range».
            models.Index(fields=["tenant", "-created_at"], name="airm_tenant_ts_idx"),
            # Per-intent breakdown for daily rollup.
            models.Index(
                fields=["tenant", "intent_classified"],
                name="airm_tenant_intent_idx",
            ),
            # Outcome distribution — success / error / fallback / escalated counts.
            models.Index(
                fields=["tenant", "outcome"],
                name="airm_tenant_outcome_idx",
            ),
            # Fallback Rate metric — `WHERE fallback_triggered = true`.
            models.Index(
                fields=["tenant", "fallback_triggered"],
                name="airm_tenant_fallback_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"AIRequestMetric[{self.id} tenant={self.tenant_id} "
            f"intent={self.intent_classified or '-'} outcome={self.outcome}]"
        )


class AIDailyMetricSummary(models.Model):
    """Per-tenant per-day rollup of `AIRequestMetric` rows (AI observability Веха 2 / #771).

    Source: `aggregate_daily_metrics(target_date, tenant)` walks all
    `AIRequestMetric` rows where `date(created_at) == target_date AND
    tenant=tenant`, computes the 4 pilot threshold metrics + counts +
    latency percentiles + cost aggregates, and writes one row here via
    `update_or_create` keyed on `(tenant, snapshot_date)`.

    Idempotent — re-running the Celery beat task for the same day yields
    the same row, same payload. Late-arriving `AIRequestMetric` rows
    (back-fills, replays) are picked up by the next aggregation pass.

    The 4 thresholds evaluated:
      - task_success_rate ≥ 0.80  (booking-correlation heuristic)
      - latency_p95_ms < 3000
      - fallback_rate < 0.20
      - mean_cost_usd < $0.01

    `threshold_status` JSONB stores per-metric verdict (`ok` / `breach`)
    for dashboard rendering (Веха 3) + audit. Empty-day handling:
    `total_requests == 0` → status = `{"no_data": true}`, no alerts fire.
    """

    THRESHOLD_OK = "ok"
    THRESHOLD_BREACH = "breach"
    THRESHOLD_NO_DATA = "no_data"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="ai_daily_metric_summaries",
        help_text="Owning tenant. PROTECT — billing-adjacent rollup history.",
    )
    snapshot_date = models.DateField(
        db_index=True,
        help_text="UTC date the rollup covers. Beat schedules at 03:00 UTC and "
        "reads yesterday's `AIRequestMetric` rows (mirrors Sprint 8 cadence).",
    )

    # ─── Counts ──────────────────────────────────────────────────────────
    total_requests = models.PositiveIntegerField(
        default=0,
        help_text="Total AI requests handled this day. Drives all rate metrics.",
    )
    success_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    fallback_count = models.PositiveIntegerField(default=0)
    escalated_count = models.PositiveIntegerField(default=0)

    # ─── Latency ─────────────────────────────────────────────────────────
    latency_p50_ms = models.PositiveIntegerField(
        default=0,
        help_text="Median latency. NULL semantics: stored as 0 when total_requests==0.",
    )
    latency_p95_ms = models.PositiveIntegerField(
        default=0,
        help_text="95th percentile latency in ms. Drives the < 3000ms threshold.",
    )
    latency_p99_ms = models.PositiveIntegerField(default=0)

    # ─── Cost ────────────────────────────────────────────────────────────
    total_tokens_input = models.PositiveBigIntegerField(default=0)
    total_tokens_output = models.PositiveBigIntegerField(default=0)
    total_cost_usd = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=0,
        help_text="Sum of `llm_cost_usd` across the day. Drives mean_cost_usd.",
    )
    mean_cost_usd = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=0,
        help_text="`total_cost_usd / total_requests`. Drives the < $0.01 threshold.",
    )

    # ─── Rate metrics ────────────────────────────────────────────────────
    task_success_rate = models.FloatField(
        default=0.0,
        help_text="`(AIRequestMetric.success_correlated_at IS NOT NULL count) / "
        "total_requests`. Drives the ≥ 0.80 threshold. Heuristic correlation "
        "via Conversation.last_booking_at within 60min of AI message.",
    )
    fallback_rate = models.FloatField(
        default=0.0,
        help_text="`fallback_count / total_requests`. Drives the < 0.20 threshold.",
    )

    # ─── Implicit feedback signal counts (Tier-A #3, sequence #6) ────────
    # Per-signal daily counts derived by the aggregation job, sourced
    # from :class:`ImplicitFeedbackSignal` rows where
    # ``recorded_at::date == snapshot_date``. Surfaced via dashboard
    # для quality-trend analysis (founder pilot validation reference).
    # All default 0 — backward-compat with pre-Tier-A-#3 rows.
    cancelled_after_suggestion_count = models.IntegerField(
        default=0,
        help_text="Count of cancellation_after_suggestion signals — "
        "bookings created after AI recommendation but cancelled within "
        "24h. Trend down = AI quality up.",
    )
    abandoned_topic_count = models.IntegerField(
        default=0,
        help_text="Count of abandoned_topic signals — conversations "
        "idle >24h after AI message без booking. Trend down = AI "
        "engagement up.",
    )
    repeat_interaction_count = models.IntegerField(
        default=0,
        help_text="Count of repeat_interaction signals — same skill "
        "invoked N+1 within session. Trend up = positive engagement.",
    )

    # ─── Per-intent breakdown ────────────────────────────────────────────
    intent_distribution = models.JSONField(
        default=dict,
        blank=True,
        help_text="`{intent_label: count}` for the day. Dashboard surfaces "
        "the top-5 intents per tenant. Empty when total_requests==0.",
    )

    # ─── Threshold status ────────────────────────────────────────────────
    threshold_status = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-metric verdict — `{metric: {value, threshold, op, status}}`. "
        "`status` in `ok`/`breach`/`no_data`. Empty when total_requests==0 "
        "(status becomes `{no_data: true}`). Dashboard reads this directly.",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the aggregation task wrote this row.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When the aggregation task last refreshed this row. Idempotent "
        "re-runs bump this without changing the payload.",
    )

    # Cross-tenant dashboard reads via `.filter(snapshot_date__gte=cutoff)` —
    # opt-out same as ShadowDeltaSnapshot pattern (apps/observability/views.py).
    _IGNORE_TENANT_MANAGER_CHECK = True

    class Meta:
        verbose_name = "AI daily metric summary"
        verbose_name_plural = "AI daily metric summaries"
        ordering = ["-snapshot_date", "tenant"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "snapshot_date"],
                name="ai_daily_metric_one_per_tenant_per_day",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "-snapshot_date"], name="aidm_tenant_date_idx"),
        ]

    def __str__(self) -> str:
        return f"AIDailyMetricSummary[{self.tenant_id} @ {self.snapshot_date}]"


class ImplicitFeedbackSignal(models.Model):
    """Per-event behavioral feedback signal (Tier-A #3, sequence #6).

    Captures user-side behavioral patterns that indicate AI quality —
    «did the suggestion stick» / «did the customer engage». Per memory
    ``project_ai_concierge_doc_extracts`` Tier-A #3 source list, scoped
    к 3 deterministic signals для pilot:

    * ``cancellation_after_suggestion`` — booking created after AI
      recommendation but cancelled within 24h. Tracks recommendation
      quality.
    * ``abandoned_topic`` — conversation idle >24h after AI message,
      no booking created. Signal that recommendation didn't resonate.
    * ``repeat_interaction`` — same skill invoked N+1 times within a
      session (per-conversation, time-windowed). Positive signal:
      customer engaged enough к ask again.

    Plus ``completed_booking_reused`` — explicitly NOT stored as new
    signal; W4's ``AIRequestMetric.success_correlated_at`` already
    captures it. The choices list includes it только для documentation
    completeness (the value will never be inserted by current code).

    NLU-requiring signals (``skipped_suggestion``, ``pursued_alternative``)
    are Phase 1 follow-up.

    ### Detection

    Daily aggregation beat (`apps.observability.ai_metrics`) extends
    its existing :func:`_correlate_task_success` walk to ALSO detect
    these signals from the same AIRequestMetric scan. Single ops
    surface; one audit row; one failure point.

    ### Privacy compliance

    Signals use METADATA only — booking event timestamps, skill names,
    conversation timing. NEVER raw user text. PII tokenizer
    (``apps/llm/pii_protected_provider.py``) wraps OpenAI/Anthropic
    calls only — orthogonal к signal capture. 152-ФЗ compliant
    by design per ADR-0011 §9 (analytics data, not PII).

    ### Aggregation

    Per-signal counts roll up daily to :class:`AIDailyMetricSummary`
    new fields (``cancelled_after_suggestion_count`` etc.). Dashboard
    consumes those counts; raw rows preserved для drill-down forensics.
    """

    # Pilot taxonomy. Order locked: deterministic-only signals first;
    # NLU signals reserved для Phase 1 enum extension.
    SIGNAL_CANCELLATION_AFTER_SUGGESTION = "cancellation_after_suggestion"
    SIGNAL_ABANDONED_TOPIC = "abandoned_topic"
    SIGNAL_REPEAT_INTERACTION = "repeat_interaction"
    # ``completed_booking_reused`` is a documentation pointer only —
    # W4's success_correlated_at is the canonical source. Listed in
    # choices so dashboard queries can union without breaking schema
    # if W4 chooses to backfill later.
    SIGNAL_COMPLETED_BOOKING_REUSED = "completed_booking_reused"

    SIGNAL_CHOICES = [
        (
            SIGNAL_CANCELLATION_AFTER_SUGGESTION,
            "Booking created → cancelled within 24h after AI suggestion",
        ),
        (SIGNAL_ABANDONED_TOPIC, "Conversation idle >24h after AI message, no booking"),
        (SIGNAL_REPEAT_INTERACTION, "Same skill invoked N+1 within session"),
        (
            SIGNAL_COMPLETED_BOOKING_REUSED,
            "Mirror of W4 success_correlated_at — reserved, not written",
        ),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="implicit_feedback_signals",
        help_text="Owning tenant. PROTECT — signal history is forensic + "
        "analytics-adjacent; dropping a tenant must not silently nuke it.",
    )
    ai_request_metric = models.ForeignKey(
        "observability.AIRequestMetric",
        on_delete=models.CASCADE,
        related_name="implicit_signals",
        help_text="Source AIRequestMetric row that triggered this signal "
        "(e.g. the AI recommendation that was later abandoned). CASCADE: if "
        "the source metric is purged (rare — metrics are forensic), the "
        "signal goes too — orphan signals have no audit context.",
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        related_name="implicit_feedback_signals",
        null=True,
        blank=True,
        help_text="Owning conversation. SET_NULL — same rationale as "
        "AIRequestMetric.conversation: 152-ФЗ forget-flow may purge the "
        "Conversation; the signal survives for analytics.",
    )
    signal_type = models.CharField(
        max_length=48,
        choices=SIGNAL_CHOICES,
        db_index=True,
        help_text="Behavioral signal kind. Drives aggregation buckets on "
        ":class:`AIDailyMetricSummary`.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Signal-specific metadata (e.g. cancellation booking "
        "event id, idle duration ms, repeat count). MUST NOT contain "
        "raw user text per ADR-0011 §9 (analytics ≠ PII).",
    )
    recorded_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When the daily aggregator detected this signal. "
        "Drives «signals detected last 7 days» dashboard query.",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Implicit feedback signal"
        verbose_name_plural = "Implicit feedback signals"
        ordering = ["-recorded_at"]
        indexes = [
            # Per-tenant timeline query («signals в last 7 days»).
            models.Index(fields=["tenant", "-recorded_at"], name="ifs_tenant_ts_idx"),
            # Per-signal-type aggregation в daily rollup.
            models.Index(fields=["tenant", "signal_type"], name="ifs_tenant_type_idx"),
            # Idempotency lookup: avoid duplicate signal на same metric.
            models.Index(
                fields=["ai_request_metric", "signal_type"],
                name="ifs_metric_type_idx",
            ),
        ]
        constraints = [
            # Idempotency invariant: one signal-type instance per
            # source metric. Re-running the daily aggregator MUST NOT
            # double-insert — uniqueness enforces this at DB level.
            models.UniqueConstraint(
                fields=["ai_request_metric", "signal_type"],
                name="ifs_metric_type_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"ImplicitFeedbackSignal[{self.signal_type} @ {self.recorded_at}]"
