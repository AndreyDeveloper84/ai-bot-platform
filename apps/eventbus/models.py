"""DomainEvent — Postgres outbox table for the dot.notation domain bus.

Distinct from :class:`apps.events.Event` (analytics, snake_case).
event-taxonomy.md §2 envelope is persisted verbatim:

    event_id ULID, event_name, event_version, occurred_at, tenant,
    actor JSON, correlation_id, causation_id, data, metadata

Outbox semantics (Q-EV-IMPL3):

  - emit() inserts is_dispatched=False
  - dispatcher claims rows in batches via SELECT FOR UPDATE SKIP LOCKED
  - successful dispatch → is_dispatched=True, dispatched_at=now()
  - failures → dispatch_attempts++ + last_error; left for retry
  - dead-letter (taxonomy §5): attempts >= MAX → flagged for triage
    (§5 procedure; out of scope for this PR)

Why no TenantScopedManager:
  emit() runs from system contexts too (worker boot, batch jobs).
  Cross-tenant reads (dispatcher) need to see all rows. Callers
  filter by tenant explicitly when they need it.
"""

from __future__ import annotations


from django.db import models


class DomainEvent(models.Model):
    """One row per emitted domain event. Outbox row → dispatcher → subscribers."""

    # event-taxonomy.md §2 envelope -----------------------------------
    event_id = models.CharField(
        max_length=26,
        primary_key=True,
        help_text="ULID per taxonomy §2. Time-sortable; outbox FIFO relies on it.",
    )
    event_name = models.CharField(
        max_length=120,
        help_text="dot.notation canonical name from taxonomy §3 catalog.",
    )
    event_version = models.CharField(
        max_length=16,
        default="1.0",
        help_text="SemVer per taxonomy §7. Bump on payload-shape change.",
    )
    occurred_at = models.DateTimeField(
        help_text="UTC. Producer-supplied (NOT auto_now) so replay can "
        "reconstruct the original moment.",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="domain_events",
        null=True,
        blank=True,
        help_text="Owning tenant. Null for system.* events (taxonomy §8).",
    )
    actor = models.JSONField(
        default=dict,
        help_text="{type, id, role} per taxonomy §2. type is always set.",
    )
    correlation_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="ULID per Q-EV-IMPL5. Empty when not part of a chain.",
    )
    causation_id = models.CharField(
        max_length=26,
        blank=True,
        default="",
        help_text="ULID of the event that caused this one. Empty otherwise.",
    )
    data = models.JSONField(
        default=dict,
        help_text="Per-event payload. NEVER raw PII — taxonomy §6 enforced "
        "at emit time; violations REJECT the row.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Implementation tags (replay flag, environment, etc.). Same PII rules as data.",
    )

    # Outbox bookkeeping ----------------------------------------------
    is_dispatched = models.BooleanField(
        default=False,
        db_index=True,
        help_text="False until the dispatcher hands the envelope to subscribers.",
    )
    dispatched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the dispatcher marks the row complete.",
    )
    dispatch_attempts = models.PositiveSmallIntegerField(
        default=0,
        help_text="Increment on each failed dispatch attempt. Dead-letter "
        "threshold per taxonomy §5 is checked by the dispatcher.",
    )
    last_error = models.TextField(
        blank=True,
        default="",
        help_text="Most recent dispatcher error message. Truncated by caller.",
    )
    dead_lettered_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Set by dispatcher when dispatch_attempts crosses MAX_ATTEMPTS. "
        "Dead-letter rows are excluded from re-claim; ops triage + replay action "
        "in admin restarts them. Phase 2.2 — replaces Phase 2.1 forever-pending "
        "workaround (taxonomy §18.5).",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="DB insertion time. Distinct from occurred_at — the "
        "former is monotonic by definition, the latter is producer time.",
    )

    objects = models.Manager()

    class Meta:
        verbose_name = "Domain event"
        verbose_name_plural = "Domain events"
        # ULID PK already gives FIFO; explicit ordering kept for admin.
        ordering = ["event_id"]
        indexes = [
            # Outbox hot path: pending rows by FIFO.
            models.Index(
                fields=["is_dispatched", "event_id"],
                name="evbus_outbox_pending_idx",
            ),
            # Per-tenant lookups in admin / analytics.
            models.Index(
                fields=["tenant", "event_name", "-occurred_at"],
                name="evbus_tenant_name_idx",
            ),
            # Correlation traversal (replay reconstruction).
            models.Index(
                fields=["correlation_id"],
                name="evbus_correlation_idx",
            ),
        ]

    @property
    def is_dead_letter(self) -> bool:
        """True iff this row has been quarantined by the dispatcher."""

        return self.dead_lettered_at is not None

    def __str__(self) -> str:
        return f"DomainEvent[{self.event_name} {self.event_id}]"
