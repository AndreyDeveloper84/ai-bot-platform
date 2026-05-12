"""Replay trace registry (DRF-497 / Sprint 5 / A1).

Per PHASE0_DESIGN §7 (F0.10): every ``pipeline.turn()`` invocation creates
``trace_id = uuid7()`` and records a ``ReplayTrace`` row with the step
snapshots. The trace is the single source of truth for "what did the
bot decide on this turn" — replay tooling reconstructs decisions from
it, regression tests assert against it, PII audits redact it.

### Sampling

Phase 0: 100% sampling in prod (1 tenant, ~1k turns/day → ~30MB/30d).
Phase 1+: drop to 10-20% when traffic grows. Sample rate is settings-
driven via :data:`config.settings.REPLAY_SAMPLE_RATE_*`.

### Retention

``expires_at`` stamped at capture (now + ``REPLAY_RETENTION_DAYS``).
The daily cleanup task (Sprint 5 / A4) hard-deletes past-expiry rows.

### PII redaction

Steps are redacted BEFORE persist via :mod:`apps.replay.redactor`
(Sprint 5 / B-track). ``redacted=True`` + ``redaction_method`` stamp
let us re-redact a backfill window if we improve the layer (Phase 1
adds NER on top of regex).

### Why TenantScopedManager

The E1 cross-tenant leakage scanner auto-discovers any model with a
``tenant`` FK + ``TenantScopedManager``. Replay traces are per-tenant
forensic data — no cross-tenant reads except via the explicit
``all_tenants`` escape hatch (admin + cleanup task).
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class ReplayTrace(models.Model):
    """One captured ``pipeline.turn()`` invocation.

    Stored as a flat row keyed by ``(tenant, trace_id)`` for fast
    lookup. ``pipeline_steps`` is the structured list of step
    snapshots; see :class:`apps.replay.recorder.TraceRecorder` for
    the writer side.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="replay_traces",
        help_text="Owning tenant. PROTECT — replay data is forensic; "
        "accidental tenant delete must not vapourise it.",
    )
    trace_id = models.CharField(
        max_length=64,
        help_text="Pipeline trace id (uuid7 stringified). Propagated via "
        "``apps.tenancy.context::current_trace_id`` ContextVar. "
        "Unique within tenant by convention; not DB-enforced because "
        "trace_id origin is the pipeline layer.",
    )
    pipeline_steps = models.JSONField(
        default=list,
        blank=True,
        help_text="List of step snapshots: input message, intent decision, "
        "safety verdict, retrieved passages (IDs + scores only — NEVER raw "
        "content), tool calls + results, draft text, final text. "
        "PII-redacted before persist by ``apps.replay.redactor``.",
    )
    redacted = models.BooleanField(
        default=True,
        help_text="True iff `pipeline_steps` passed through the redactor. "
        "Phase 0 always True — recorder always redacts. Field exists to "
        "support a future raw-trace mode for testing (never prod).",
    )
    redaction_method = models.CharField(
        max_length=32,
        default="",
        blank=True,
        help_text="Identifier of the redactor version that processed this "
        'row (e.g. "regex_v1"). Lets a future migration re-redact rows '
        "captured under an older method without raw-text access.",
    )
    captured_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text="Hard-delete deadline. Stamped by the recorder at capture "
        "time as ``captured_at + REPLAY_RETENTION_DAYS``. The daily Celery "
        "cleanup task evicts past-expiry rows (Sprint 5 / A4).",
    )

    # Default manager scopes to current_tenant(). E1 scanner auto-picks
    # this up. ``all_tenants`` is the explicit cross-tenant escape hatch
    # for admin + cleanup.
    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Replay trace"
        verbose_name_plural = "Replay traces"
        ordering = ["-captured_at"]
        indexes = [
            # Per-tenant timeline (admin browse + replay tooling lookups).
            models.Index(fields=["tenant", "-captured_at"]),
            # Trace-id lookup — runner.py + diff CLI grep by trace_id.
            models.Index(fields=["trace_id"]),
            # Cleanup task: SELECT WHERE expires_at < now() — index keeps
            # the daily eviction sweep cheap regardless of row count.
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"ReplayTrace[{self.trace_id[:8]}@{self.tenant_id}]"
