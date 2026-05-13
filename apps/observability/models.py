"""Observability persistence models (Sprint 8 / S3 / DRF-718).

One model — :class:`ShadowDeltaSnapshot`. Stores per-day per-tenant
agreement metrics so the dashboard (D1) + the strict-scope flip gate
(F1) can read a stable history without recomputing.

Phase 0 ships a single row per (tenant, date); Sprint 9 may need a
finer grain (per-hour for canary diagnostics). Keep the constraint
loose now and tighten when use cases emerge.
"""

from __future__ import annotations

import uuid

from django.db import models


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
