"""Observability persistence models.

Two models:

  - :class:`ShadowDeltaSnapshot` (Sprint 8 / S3 / DRF-718) — per-day
    per-tenant agreement metrics from shadow-mode A/B comparison.
    Dashboard (D1) + strict-scope flip gate (F1) read history.

  - :class:`AIRequestMetric` (Веха 1 of AI observability epic #769) —
    per-AI-request metric row capturing intent / skill / latency /
    tokens / cost / outcome. Source data for daily aggregation
    (Веха 2 → :class:`AIDailyMetricSummary`) and dashboard
    (Веха 3 → admin view).
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
