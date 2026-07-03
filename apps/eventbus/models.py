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

    # Tenancy system-check opt-out (tenancy.W900 / W901). DomainEvent is
    # an outbox table — the dispatcher reads cross-tenant via
    # ``select_for_update(skip_locked=True)`` (see apps/eventbus/
    # dispatcher.py) because it's a system process, not a tenant-scoped
    # request. Adding TenantScopedManager would scope dispatch reads to
    # the (None) current_tenant and silently drop pending rows.
    # Documented intentional deviation.
    _IGNORE_TENANT_MANAGER_CHECK = True

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


# ---------------------------------------------------------------------------
# Cross-service ingest models — `docs/architecture/event-contract.md`
# §5 (idempotency dedupe table) + §6.4 (dead-letter table).
#
# DISTINCT from :class:`DomainEvent` above which models the IN-PROCESS
# bus. These two models persist state for the inbound cross-service
# channel from Ayla djangoproject.
# ---------------------------------------------------------------------------


class IngestDedupe(models.Model):
    """Idempotency ledger for cross-service inbound events.

    `event-contract.md` §5.1 — consumers dedupe on ``event_id``. A row
    here means an event has been processed end-to-end (the consumer's
    side-effect + this row are written in the SAME DB transaction per
    §5.1, so a crash before COMMIT rolls both back and a retry
    re-processes cleanly).

    Retention: §5.3 says ≥120 days (max of DLQ retention 90d + dedupe
    safety margin 30d). Cleanup is a Celery beat (out of scope for
    #432 itself; filed as a follow-up).
    """

    # Same opt-out as DomainEvent above — system reads cross-tenant.
    _IGNORE_TENANT_MANAGER_CHECK = True

    event_id = models.CharField(
        max_length=36,
        primary_key=True,
        help_text="Cross-service envelope §2.event_id. varchar(36) holds "
        "both a bare ULID (26 chars) and Ayla's uuid4() (36 chars) — the "
        "spec says ULID but the outbox emits uuid4 in practice (#1058). "
        "PK so concurrent inserts hit a uniqueness constraint "
        "rather than racing in application code.",
    )
    event_name = models.CharField(max_length=120)
    event_version = models.IntegerField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(
        help_text="Set to ``now()`` in the same transaction as the consumer's side-effect."
    )

    class Meta:
        verbose_name = "Ingest dedupe row"
        verbose_name_plural = "Ingest dedupe ledger"
        indexes = [
            models.Index(fields=["received_at"], name="evbus_ingest_dedupe_recv_idx"),
        ]

    def __str__(self) -> str:
        return f"IngestDedupe[{self.event_id}]"


class IngestDLQ(models.Model):
    """Dead-letter table for cross-service inbound events.

    `event-contract.md` §6.4 + §8.4/§8.5 — events that fail processing
    after retries OR carry an unknown ``event_name`` / unknown
    ``event_version`` are persisted here for operator investigation.

    Retention: §6.4 says 90 days. Cleanup is a Celery beat (follow-up).
    Manual replay tooling: §5.3 mentions an ``X-Ayla-Force-Process``
    header; that's a follow-up ticket — the table shape is fixed now
    so replay tooling can land separately without a migration.
    """

    _IGNORE_TENANT_MANAGER_CHECK = True

    id = models.BigAutoField(primary_key=True)
    event_id = models.CharField(
        max_length=36,
        db_index=True,
        help_text="Cross-service envelope §2.event_id (bare ULID 26 chars or "
        "Ayla uuid4 36 chars, #1058). Indexed for replay lookups. Over-length "
        "ids are rejected fail-fast before reaching this table (see "
        "ingest_dispatcher event_id_too_long guard); the DLQ writer truncates "
        "defensively so a contract-violating id cannot turn the DLQ write "
        "itself into a DataError.",
    )
    event_name = models.CharField(max_length=120, db_index=True)
    event_version = models.IntegerField()
    reason = models.CharField(
        max_length=64,
        help_text="Slug from `event-contract.md` §8 (e.g. ``unknown_event_name``, "
        "``unknown_event_version``, ``handler_exception``, ``retry_budget_exhausted``).",
    )
    raw_body = models.JSONField(
        help_text="Full envelope payload at receipt. PII rules §7 already "
        "limit what may appear here, but operators MUST treat this table "
        "as 152-ФЗ-sensitive nonetheless."
    )
    dead_lettered_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Time the row was first dead-lettered. ``auto_now_add`` "
        "(NOT ``auto_now``) is intentional: with the #433 upsert "
        "pattern, subsequent failures past threshold REFRESH the row "
        "via ``update_or_create`` but MUST NOT overwrite this "
        "timestamp. Operator triage cares about «when did this event "
        "first fail», not «when was the most recent retry». The most-"
        "recent-retry signal lives on HandlerFailureTracker.last_attempt_at.",
    )
    replayed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when an operator manually re-publishes the entry. "
        "Distinct from received-during-replay; the latter goes through "
        "the normal dedupe ledger above.",
    )

    class Meta:
        verbose_name = "Ingest DLQ entry"
        verbose_name_plural = "Ingest DLQ entries"
        indexes = [
            models.Index(fields=["-dead_lettered_at"], name="evbus_dlq_recent_idx"),
            models.Index(fields=["reason"], name="evbus_dlq_reason_idx"),
        ]
        constraints = [
            # #433 umbrella — upsert key for ``_write_dlq``. Distinct
            # reasons share an ``event_id`` (a malformed envelope can
            # produce both ``unknown_event_name`` and a later
            # ``handler_exception`` after a payload retry), so the
            # composite is the right shape. Round-1 DLQ work landed
            # this without a constraint; #433 closes the gap so the
            # HANDLER_EXCEPTION-threshold path can do
            # ``update_or_create`` instead of racing.
            models.UniqueConstraint(
                fields=["event_id", "reason"],
                name="evbus_dlq_event_reason_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"IngestDLQ[{self.event_name} {self.event_id} {self.reason}]"


class HandlerFailureTracker(models.Model):
    """Per-(event_id, handler) attempt counter for #433 umbrella.

    Problem the model solves: ``HANDLER_EXCEPTION`` outcomes today
    only surface in the application log + Sentry. Ayla retries per
    §6.3 — but bot-platform has no DB record of «this event has
    failed N times». Operator triage means digging through log
    aggregator with no row-level handle.

    Solution: an attempt counter that survives outside the
    handler's transaction (incremented in its own atomic block
    AFTER the handler's transaction rolled back). When the counter
    crosses ``settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD``,
    the dispatcher upserts a DLQ row with ``reason="handler_exception"``.

    Why not store on ``IngestDedupe``: that table only gets a row
    on successful processing (same-transaction with the handler).
    On exception the row never lands. We need a parallel counter
    that survives rollback.

    ### Retention

    Same 120-day window as IngestDedupe (§5.3). Cleanup is a Celery
    beat (FOLLOW_UP).

    ### Forensic trace

    ``last_error`` is truncated at 1024 chars and contains only the
    exception class + message — NEVER the full stack trace (that's
    in the log aggregator). PII-bearing exception messages are an
    upstream contract bug; we accept best-effort truncation here.
    """

    _IGNORE_TENANT_MANAGER_CHECK = True

    id = models.BigAutoField(primary_key=True)
    event_id = models.CharField(
        max_length=36,
        help_text="Cross-service envelope §2.event_id (bare ULID 26 chars or "
        "Ayla uuid4 36 chars, #1058).",
    )
    handler_name = models.CharField(
        max_length=140,
        help_text="``{event_name}@v{event_version}`` — the registered "
        "handler whose invocation raised. Distinct handlers for the "
        "same event_id (theoretical multi-handler future) keep "
        "independent counters.",
    )
    outcome = models.CharField(
        max_length=32,
        default="handler_exception",
        help_text="Future-proof for richer outcomes (timeout, "
        "permanent_failure). MVP only writes ``handler_exception``.",
    )
    attempt_count = models.PositiveIntegerField(
        default=0,
        help_text="Count of failed dispatch attempts for this "
        "(event_id, handler, outcome) triple. Incremented on every "
        "HANDLER_EXCEPTION; reset only by operator action.",
    )
    last_error = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        help_text="Truncated ``exc_type: str(exc)`` from the most "
        "recent failure. Stack traces live in the log aggregator.",
    )
    first_attempt_at = models.DateTimeField(auto_now_add=True)
    last_attempt_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Handler failure tracker"
        verbose_name_plural = "Handler failure tracker"
        constraints = [
            models.UniqueConstraint(
                fields=["event_id", "handler_name", "outcome"],
                name="evbus_handler_failure_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["-last_attempt_at"],
                name="evbus_handler_fail_recent_idx",
            ),
            models.Index(
                fields=["handler_name"],
                name="evbus_handler_fail_name_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"HandlerFailureTracker[{self.handler_name} {self.event_id} "
            f"attempts={self.attempt_count}]"
        )


class PaymentTerminalDedupe(models.Model):
    """Per-payment terminal-state dedupe for #443 payment consumer.

    Round-1 adversarial BLOCKER-1: two ``payment.captured`` (or
    ``payment.refunded``) events with DIFFERENT ``event_id`` but the
    SAME ``payment_id`` would bypass the handler-level
    ``Conversation.last_payment_event_id`` short-circuit and double-fire
    ``loyalty_bonus_eligible`` / ``loyalty_refund_reverse`` — pushing the
    loyalty subscriber (Epic #289) into a double-accrual / double-reverse
    state. Real triggers:

    * Ayla operator manually re-fires after a Sentry alert with a fresh
      ULID.
    * Outbox publisher loses dedupe state on a DB failover and re-mints.

    This table is a SECOND idempotency layer, keyed by ``(payment_id,
    terminal_state)`` rather than by ``event_id``. ``IngestDedupe``
    remains the primary canonical guard; this one specifically protects
    the terminal-state fan-out (captured / refunded) where loyalty
    side-effects compound.

    NOT used for ``payment.authorized`` (not terminal — pending slot
    can be re-issued) or ``payment.failed`` (intentionally counts
    repeat failures via ``consecutive_payment_failures``).

    Retention: same 120-day window as IngestDedupe (§5.3). Cleanup is
    a Celery beat (follow-up).
    """

    _IGNORE_TENANT_MANAGER_CHECK = True

    class TerminalState(models.TextChoices):
        CAPTURED = "captured", "Captured"
        REFUNDED = "refunded", "Refunded"

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(
        help_text="Tenant scoping the dedupe row. Round-2 NEW-1: without "
        "this, a malicious tenant could pre-claim a victim's "
        "(payment_id, terminal_state) globally and block the victim's "
        "legitimate loyalty fan-out forever — category-2 cross-tenant "
        "DoS. UUIDField (not FK) — this is a system-level dedupe "
        "ledger; we keep CASCADE semantics out of the picture and "
        "rely on retention sweeps for cleanup.",
    )
    payment_id = models.UUIDField(
        help_text="Canonical Ayla Payment.id from the event payload.",
    )
    terminal_state = models.CharField(
        max_length=16,
        choices=TerminalState.choices,
        help_text="Which terminal state we've already processed for "
        "this payment_id. A row's existence is the dedupe signal.",
    )
    event_id = models.CharField(
        max_length=36,
        help_text="Cross-service event_id (ULID 26 or Ayla uuid4 36, #1058) "
        "of the event that first reached this terminal state. Forensic "
        "trace — distinct events with the same (tenant_id, payment_id, "
        "terminal_state) tuple after this row exists are no-op'd at the "
        "handler layer.",
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Payment terminal dedupe row"
        verbose_name_plural = "Payment terminal dedupe ledger"
        constraints = [
            # Primary dedupe guarantee: at most one row per
            # (tenant_id, payment_id, terminal_state). Round-2 NEW-1
            # fix: tenant_id in the key blocks cross-tenant pre-claim
            # DoS.
            models.UniqueConstraint(
                fields=["tenant_id", "payment_id", "terminal_state"],
                name="evbus_payment_terminal_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["processed_at"], name="evbus_payment_term_proc_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"PaymentTerminalDedupe[t={self.tenant_id} p={self.payment_id} {self.terminal_state}]"
        )


class ReviewProcessedDedupe(models.Model):
    """Per-review dedupe for #445 review.created consumer.

    Per event-contract.md §3.9 step 1: «idempotent — same review_id
    MUST NOT double-count». The dispatcher's :class:`IngestDedupe`
    table dedupes on ``event_id``, which is necessary but not
    sufficient — an operator manual re-fire with a fresh ULID would
    bypass it and double-count the sentiment update.

    Same pattern as :class:`PaymentTerminalDedupe` (#443):
    ``IngestDedupe`` is the primary canonical guard; this one
    specifically protects the review fan-out (ClientProfile sentiment
    bump) where double-application would distort RFM-adjacent state.

    Retention: same 120-day window as IngestDedupe (§5.3). Cleanup is
    a Celery beat (follow-up).
    """

    _IGNORE_TENANT_MANAGER_CHECK = True

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(
        help_text="Tenant scoping the dedupe row. Mirrors the "
        "PaymentTerminalDedupe.tenant_id pattern (#443 Round-2 NEW-1) "
        "so a malicious tenant cannot pre-claim a victim's review_id.",
    )
    review_id = models.UUIDField(
        help_text="Canonical Ayla Review.id from the event payload.",
    )
    event_id = models.CharField(
        max_length=36,
        help_text="Cross-service event_id (ULID 26 or Ayla uuid4 36, #1058) "
        "of the event that first processed this review. Forensic trace — "
        "distinct events with the same (tenant_id, review_id) after this "
        "row exists are no-op'd.",
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Review processed dedupe row"
        verbose_name_plural = "Review processed dedupe ledger"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "review_id"],
                name="evbus_review_processed_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["processed_at"],
                name="evbus_review_proc_proc_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"ReviewProcessedDedupe[t={self.tenant_id} r={self.review_id}]"


class NotificationDispatchDedupe(models.Model):
    """Exactly-once guard for consumer→skill→channel notifications (#927).

    A consumer (e.g. ``payment.failed``) dispatches a customer DM via an
    in-process skill call. The consumer's ``event_id`` short-circuit
    already blocks a same-``event_id`` re-run, but the channel send is
    the FINAL, non-rollback-able side-effect: a Redis-Streams retry or a
    future code change that weakens the short-circuit could fire a
    DUPLICATE personalized notification. For ``payment.failed`` that is a
    high-stress-context message and a 152-ФЗ-relevant repeated automated
    contact (issue #927, baseline G6.2 / codex P0-4).

    A row's existence == "this exact notification was already dispatched".
    Claim it (create) BEFORE invoking the skill; an ``IntegrityError`` on
    the unique key means a prior dispatch already won — skip.

    Tenant-scoped key (mirrors ``PaymentTerminalDedupe`` Round-2 NEW-1):
    without ``tenant_id`` in the key, a malicious tenant could pre-claim a
    victim's ``(event_id, recipient, channel)`` and suppress the victim's
    legitimate notification — a cross-tenant DoS. ``event_id`` is globally
    unique in practice, but the tenant scoping makes the dedupe ledger
    itself unweaponizable cross-tenant.

    Pilot scope: eventbus-level guard (Gamma). A channel-side SETNX in
    ``apps.channels.max.outbound`` (exactly-once for ALL notification
    kinds) is the post-pilot target — see #927.

    Retention: same sweep family as the other dedupe ledgers (follow-up).
    """

    _IGNORE_TENANT_MANAGER_CHECK = True

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(
        help_text="Tenant scoping the dedupe row — blocks cross-tenant "
        "pre-claim suppression of a victim's notification. UUIDField "
        "(not FK): system-level ledger, cleaned by retention sweeps.",
    )
    event_id = models.CharField(
        max_length=36,
        help_text="Cross-service event_id (ULID 26 or Ayla uuid4 36, #1058) "
        "of the event that triggered the notification. Part of the "
        "exactly-once key.",
    )
    recipient_id = models.UUIDField(
        help_text="Canonical Ayla User.id the notification is sent to.",
    )
    channel = models.CharField(
        max_length=32,
        help_text="Delivery channel (e.g. ``max``). A given event may "
        "legitimately notify the same user on different channels.",
    )
    kind = models.CharField(
        max_length=64,
        help_text="Notification kind (e.g. ``payment_failed``). Forensic "
        "label — NOT part of the unique key (one event → one notification "
        "per channel regardless of kind).",
    )
    dispatched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Notification dispatch dedupe row"
        verbose_name_plural = "Notification dispatch dedupe ledger"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "event_id", "recipient_id", "channel"],
                name="evbus_notif_dispatch_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["dispatched_at"], name="evbus_notif_disp_at_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"NotificationDispatchDedupe[t={self.tenant_id} e={self.event_id} "
            f"r={self.recipient_id} {self.channel}/{self.kind}]"
        )
