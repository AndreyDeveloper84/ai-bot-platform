"""Booking persistence layer (DRF-838 / Phase 1 / B2).

Two models — :class:`BookingRequest` and :class:`BookingReminder` — ported
from ``mysite/services_app/models.py`` (the prod-validated salon stack).
Shapes are kept verbatim where the mysite contract is observable from
outside (audit consumers, admin tables, reminder Celery jobs), and
adapted where multi-tenancy requires it.

### Why bundled into the webhook PR (B2)

The YClients admin webhook (:mod:`apps.integrations.yclients.webhooks`)
calls :meth:`BookingRequest.objects.create` and
:meth:`BookingReminder.objects.update_or_create`. Landing the webhook
before the schema exists would leave the port half-wired — Postgres
would 500 on the first event and YClients would retry forever (the
"always 200" guard catches the exception, but the row never persists
and the reminders system never fires). Bundling the minimal model
definitions here is strictly necessary; the BOOKING **skill** (LLM-
callable tools that create rows from a client-side chat) lands in B3
on top of these models without modifying them.

### Multi-tenant compatibility

Both models include a ``tenant`` FK + :class:`TenantScopedManager`
default manager — same pattern as :class:`apps.kb.models.KbDocument`.
Cross-tenant code paths (admin sweep, replay tooling) must use the
``all_tenants`` escape-hatch manager.

### Idempotency contract

:attr:`BookingReminder.yclients_record_id` + :attr:`BookingReminder.kind`
form a unique pair (``unique_together``) — exactly mirrors the mysite
constraint so the webhook handler's ``update_or_create`` path is a
single statement, re-deliverable any number of times without creating
duplicates.

### Status choices

``BookingReminder.Status`` enumerates the full reminder lifecycle that
the existing mysite Celery beats produce (``send_due_reminders`` +
``escalate_stale_reminders``). The B2 webhook only writes the
``PENDING`` and ``CANCELLED`` terminal values; B3 handlers will move
rows through ``SENT_NO_REPLY`` / ``SENT`` / ``CONFIRMED`` / etc.
Locking the full enum now means B3 lands without a schema migration.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


BOOKING_SOURCE_CHOICES = [
    ("wizard", "Wizard form on site"),
    ("bot", "Bot chat flow"),
    ("yclients_admin", "YClients admin (salon side)"),
    ("import", "Bulk import"),
]


ATTRIBUTION_BOOKING_SOURCE_CHOICES = [
    ("ai_direct", "AI created booking directly"),
    ("ai_assisted", "AI participated; human finished"),
    ("human_direct", "Salon staff only, no AI"),
    ("external", "From outside (YClients UI, phone)"),
    ("test_admin", "Test or admin/master-created"),
]

ATTRIBUTION_ACTOR_TYPES = {
    "customer",
    "owner",
    "admin",
    "receptionist",
    "master",
    "system",
}


class BookingRequest(models.Model):
    """A booking intent — created by the wizard, the bot skill, or the
    YClients admin webhook (when a salon employee books a client via the
    YClients UI for a client we have a BotUser for).

    Mirrors ``mysite/services_app/models.py::BookingRequest``. Field
    names kept verbatim (``service_name``, ``master_name``,
    ``client_name``, ``client_phone``, ``comment``, ``source``,
    ``is_processed``, ``created_at``) so existing analytics + admin
    queries port over without code change.

    Deviations from mysite:

    * ``tenant`` FK added — multi-tenant by construction.
    * ``bot_user`` FK now points at :class:`apps.identity.models.BotUser`
      (channel-scoped identity), not mysite's single-channel BotUser.
    * Soft-delete column intentionally NOT added — the table is
      append-only by design (analytics relies on it). Cancellation
      goes through :attr:`BookingRequest.status` transitions
      (B5 / DRF-841 — adds ``cancelled`` / ``rescheduled`` states so
      ``cancel_booking`` / ``reschedule_booking`` tools have a place
      to flip lifecycle without rewriting history). Old paths that
      relied on ``BookingReminder.Status.CANCELLED`` to imply booking
      cancellation continue to work — the two statuses are
      complementary signals.

    Indexes mirror what mysite admin actually scans for:

    * ``(tenant, -created_at)`` — admin "latest bookings" view
    * ``(tenant, bot_user)`` — "all bookings by this client" drill-down
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="booking_requests",
        help_text="Owning tenant. PROTECT — bookings are a forensic / "
        "billing artefact; deleting a tenant requires explicit data "
        "purge first (audit retention requirement).",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="booking_requests",
        help_text="Linked channel identity. SET_NULL — the booking row "
        "is still a valuable analytics record after the BotUser is "
        "purged (GDPR right-to-erasure scenario).",
    )

    # ── Snapshot fields (denormalised at write time, never edited) ────────
    # These deliberately duplicate data from BotUser / catalog rows so
    # the booking is a self-contained audit artefact: rename a service
    # or a master in the catalog and historic bookings still say what
    # the client actually saw at booking time.
    category_name = models.CharField(max_length=200, blank=True, default="")
    service_name = models.CharField(max_length=200)
    master_name = models.CharField(max_length=150, blank=True, default="")
    client_name = models.CharField(max_length=150)
    client_phone = models.CharField(max_length=30)

    comment = models.TextField(blank=True, default="")
    is_processed = models.BooleanField(
        default=False,
        help_text="Admin-side flag. Wizard rows start False (operator "
        "calls back); yclients_admin webhook rows start True (the "
        "admin already booked it in YClients).",
    )
    source = models.CharField(
        max_length=20,
        choices=BOOKING_SOURCE_CHOICES,
        default="wizard",
    )

    class Status(models.TextChoices):
        """Lifecycle of a booking row.

        Added in B5 / DRF-841 so customer-initiated ``cancel_booking``
        and ``reschedule_booking`` tools have a place to flip state
        without rewriting history. Pre-B5 rows default to ``CONFIRMED``
        (the implicit prior state — every row was created by a
        successful YClients write).

        * ``CONFIRMED``    — initial state on create (was implicit before).
        * ``CANCELLED``    — customer cancelled (B5 ``cancel_booking``).
        * ``RESCHEDULED``  — old row after ``reschedule_booking``
                             cancel-and-create; new BookingRequest row
                             is created as ``CONFIRMED`` and links via
                             the comment marker.

        ``RESCHEDULED`` is a terminal label on the OLD row — the
        actual "this is your booking now" lives on the new row. The
        old row is preserved (not deleted) so analytics can compute
        reschedule rates per master / service.
        """

        CONFIRMED = "confirmed", "Confirmed"
        CANCEL_REQUESTED = (
            "cancel_requested",
            "Cancel requested (within undo window)",
        )
        RESCHEDULE_REQUESTED = (
            "reschedule_requested",
            "Reschedule requested (candidate stashed)",
        )
        CANCELLED = "cancelled", "Cancelled"
        RESCHEDULED = "rescheduled", "Rescheduled (replaced)"

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Stamped by apps.bookings.tasks.detect_completed_bookings "
        "when the periodic scan finds the visit has ended (visit_at + "
        "duration + grace < now()) AND status is still CONFIRMED. Acts as "
        "the idempotency lock for the booking.completed eventbus producer: "
        "the task SET ... WHERE completed_at IS NULL — rowcount tells "
        "whether we won the race. NULL on rows where the visit hasn't "
        "happened yet, was cancelled, or where the producer hasn't run yet.",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.CONFIRMED,
        db_index=True,
        help_text=(
            "Lifecycle: confirmed (default) → cancel_requested → "
            "cancelled OR reschedule_requested → rescheduled. "
            "cancel_requested and reschedule_requested are interim "
            "states reversible by the customer."
        ),
    )
    cancel_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Set when state flips to CANCEL_REQUESTED. Used to compute "
            "the undo window expiry (5s per customer-cancellation-"
            "reschedule-spec §3.4 / Q-CR1)."
        ),
    )
    rescheduled_from = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rescheduled_into",
        help_text=(
            "Self-FK: the old BookingRequest this row replaced after a "
            "customer-initiated reschedule. PROTECT so the audit linkage "
            "cannot be silently broken by deleting the old row."
        ),
    )
    commercial_identity_snapshot = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Q12-α #541 (founder ACK 2026-05-23) — snapshot of the "
            "service's commercial identity at booking write time. Used "
            "by reschedule continuation chain to break when the salon "
            "admin mutates the service (price hike, duration change, "
            "currency swap, FK relink). Schema: {service_id, "
            "service_name, sticker_price_amount, currency, "
            "duration_minutes}. Only 4 fields are compared "
            "(service_name is informational). Pre-#541 rows have NULL "
            "→ chain check skipped per memory "
            "q12a-billing-founder-gate (legacy chains preserved)."
        ),
    )
    original_booking_event = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reschedule_continuations",
        # Tech-lead double-pass S4: db_index=False so the FK constraint
        # adds without an auto-index. Migration 0010 then creates the
        # index via ``CREATE INDEX CONCURRENTLY`` to avoid the multi-
        # second AccessExclusiveLock that ``AddField(db_index=True)``
        # would take on prod-tenant booking tables with >100k rows.
        db_index=False,
        help_text=(
            "Q12-α continuation-chain root (issue #478, founder ACK "
            "2026-05-22). NULL when this row starts a new billable chain "
            "(fresh ai_direct booking OR a reschedule that broke the "
            "chain via service_swap / >90d / partial-failure terminal). "
            "Set to the chain ROOT's id when this row is a continuation "
            "(same service, ≤90d from root, no intervening cancel). "
            "PROTECT so a deleted root can't silently orphan the chain. "
            "Always points at the ROOT, never at an intermediate link — "
            "see apps/booking/services/attribution.py::"
            "compute_reschedule_continuation."
        ),
    )
    reschedule_candidate = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Stashed candidate slot for an in-flight reschedule: "
            "{'new_master_id', 'new_service_id', 'new_visit_at'}. "
            "Cleared on commit/abandon."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # --- 4a additions: visit time + catalog FKs + attribution ----------
    visit_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the appointment is scheduled. Required for new "
        "(non-external) rows; legacy backfilled rows have NULL.",
    )
    duration_min = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Service duration at booking time (snapshot).",
    )
    service = models.ForeignKey(
        "catalog.CatalogService",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="booking_requests",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="booking_requests",
    )
    booking_source = models.CharField(
        max_length=20,
        choices=ATTRIBUTION_BOOKING_SOURCE_CHOICES,
        default="external",
        db_index=True,
        help_text="Per attribution-policy. ai_direct only is billable.",
    )
    ai_assist_score = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0,
        help_text="0.00–1.00 internal analytics estimate of AI contribution.",
    )
    billable = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Locked rule: True iff booking_source='ai_direct' AND "
        "status=='confirmed'. Set once, never recomputed.",
    )
    billing_reason = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Required when billable=True (audit trail).",
    )
    attribution_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Audit context. Required key: actor_type (validator).",
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )

    # Phase 4 / F5 — post-visit feedback. Filled when the customer
    # submits the rating form; before that all four fields are NULL.
    # ``rating <= 3`` triggers a HUMAN_LOCKED handoff (apps.handoff
    # AdminTask + Conversation.state=HUMAN_HANDOFF), see
    # apps/booking/services/feedback.py.
    rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Customer rating 1-5. NULL until they submit the F5 form. "
        "Values <= 3 fire the handoff flow.",
    )
    feedback_comment = models.TextField(
        blank=True,
        default="",
        help_text="Optional free-text comment from the F5 form (≤500 chars enforced at the API).",
    )
    feedback_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Wall-clock when the rating was submitted. Idempotency anchor: "
        "submit_feedback raises 'already_rated' when this is non-NULL.",
    )
    feedback_prompt_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the post-visit DM prompt was dispatched. NULL = not yet sent. "
        "Phase 4b will scan WHERE NULL and visit_at < now()-1h.",
    )

    def save(self, *args, **kwargs):
        self._validate_attribution_metadata()
        super().save(*args, **kwargs)

    def _validate_attribution_metadata(self) -> None:
        """Reject saves with missing/invalid attribution metadata when
        the writer opted into the new model (booking_source != 'external').

        Per attribution-policy.md §4 + Q-ATT-IMPL3.
        Legacy writers (admin webhook, bot tools, reminders pre-port)
        leave booking_source='external' and bypass the validator.
        """

        if self.booking_source == "external":
            return

        from django.core.exceptions import ValidationError

        meta = self.attribution_metadata or {}
        actor_type = meta.get("actor_type")
        if actor_type is None:
            raise ValidationError(
                "attribution_metadata.actor_type is required (per attribution-policy.md §4)"
            )
        if actor_type not in ATTRIBUTION_ACTOR_TYPES:
            raise ValidationError(
                f"attribution_metadata.actor_type {actor_type!r} not in "
                f"{sorted(ATTRIBUTION_ACTOR_TYPES)}"
            )
        if self.visit_at is None:
            raise ValidationError(
                f"visit_at is required when booking_source={self.booking_source!r} (Q-ATT-IMPL3)"
            )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Booking request"
        verbose_name_plural = "Booking requests"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "bot_user"]),
            models.Index(fields=["tenant", "status"]),
        ]
        # 4a-hardening + customer-cancel-reschedule spec §2 partial
        # UNIQUE on (master, visit_at) WHERE status is one of the
        # "this row still holds the slot" states — DB-level backstop
        # against double-bookings even mid-cancel-undo.
        constraints = [
            models.UniqueConstraint(
                fields=["master", "visit_at"],
                condition=models.Q(
                    status__in=(
                        "confirmed",
                        "cancel_requested",
                        "reschedule_requested",
                    ),
                    visit_at__isnull=False,
                ),
                name="booking_unique_master_active_visit_at",
            ),
            # Phase 4 — rating must be NULL or in 1..5.
            models.CheckConstraint(
                condition=models.Q(rating__isnull=True) | models.Q(rating__gte=1, rating__lte=5),
                name="booking_rating_in_range_or_null",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.client_name} — {self.service_name} ({self.created_at:%d.%m.%Y %H:%M})"


class BookingReminder(models.Model):
    """A scheduled reminder for a YClients booking.

    Two reminders per booking (``DAY_BEFORE`` / ``TWO_HOURS``) are written
    by the webhook handler on a ``record.create`` event. The send-due
    Celery beat (B3 / B4 — out of scope for this PR) picks rows where
    ``status=PENDING AND scheduled_at <= now()`` and dispatches via the
    channel-agnostic outbound layer.

    ### Idempotency

    ``unique_together(yclients_record_id, kind)`` is the cornerstone of
    re-delivery safety. The webhook uses ``update_or_create`` keyed on
    this pair, so a duplicate ``record.create`` payload from YClients
    rewrites the same two rows instead of doubling the schedule.

    ### Status lifecycle (full vocabulary locked at B2)

    The B2 webhook only writes ``PENDING`` (on create / update) and
    ``CANCELLED`` (on delete). The other states (``SENT_NO_REPLY``,
    ``SENT``, ``CONFIRMED``, ``RESCHEDULE_REQUESTED``, ``ESCALATED``,
    ``FAILED``) are written by B3 / B4 Celery handlers — listed here so
    B3 doesn't need a schema migration.

    ### chat_id snapshot

    ``chat_id`` is stored on the reminder (snapshot from BotUser at
    write time) deliberately: BotUser rows can change channel
    identifier on cross-device login or be purged for GDPR; the
    reminder's send target must stay stable to the booking-time value.
    Same rationale as mysite.

    ### Delete semantics

    ``record.delete`` events transition existing reminders to
    ``CANCELLED`` instead of hard-deleting — preserves the audit trail
    for "why didn't the reminder fire?" investigations and the
    analytics pipeline (cancellation rate by master / hour / service).
    """

    class Kind(models.TextChoices):
        DAY_BEFORE = "day_before", "T-24h (day before)"
        TWO_HOURS = "two_hours", "T-2h (two hours before)"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending dispatch"
        SENT_NO_REPLY = "sent_no_reply", "Sent, no client reply yet"
        SENT = "sent", "Sent (T-2h, no buttons)"
        CONFIRMED = "confirmed", "Client confirmed"
        RESCHEDULE_REQUESTED = "reschedule", "Client requested reschedule"
        CANCELLED = "cancelled", "Client / admin cancelled"
        ESCALATED = "escalated", "Escalated to human operator"
        FAILED = "failed", "Send error"
        # P0 PRE_PILOT — send-time booking-state re-check invariant.
        # Distinct from CANCELLED (which means «B2 webhook said cancelled»).
        # STALE_DROPPED means «при dispatch обнаружили что underlying
        # BookingRequest изменилось (cancelled / rescheduled / completed)
        # ИЛИ became invalid между schedule + dispatch». Auditable как
        # отдельный signal для post-pilot analytics (stale-rate spike
        # detection per master / service / time-of-day).
        STALE_DROPPED = "stale_dropped", "Dropped at dispatch (booking changed)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="booking_reminders",
        help_text="Owning tenant. PROTECT mirrors BookingRequest.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.CASCADE,
        related_name="booking_reminders",
        help_text="The recipient. CASCADE: if the BotUser is purged "
        "(GDPR erasure), the reminders for that person must go too — "
        "an orphaned reminder would have nowhere to send.",
    )
    booking_request = models.ForeignKey(
        "BookingRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reminders",
        help_text="Optional link to the source BookingRequest row "
        "(present when the reminder was created by our admin webhook).",
    )
    yclients_record_id = models.CharField(
        max_length=64,
        db_index=True,
        null=True,
        blank=True,
        help_text="YClients record id (stringified — YClients ints fit "
        "fine but the API will return larger opaque ids on enterprise "
        "tenants per their roadmap). Nullable so the Ayla consumer path "
        "(#442) writes NULL here while the legacy unique_together "
        "(yclients_record_id, kind) stays non-conflicting — NULL ≠ NULL "
        "in Postgres uniqueness, so two Ayla appointments with the same "
        "kind don't collide.",
    )
    chat_id = models.CharField(
        max_length=128,
        help_text="Snapshot of BotUser.chat_id at write time. "
        "Snapshot — chat_id may change in BotUser later.",
    )
    ayla_appointment_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Ayla appointment UUID per ADR-0009 event contract. "
        "When set, replaces yclients_record_id as the idempotency key "
        "for Ayla-side reminders (#442 booking consumer). Coexists with "
        "the YClients legacy path: rows from the YClients webhook keep "
        "yclients_record_id + leave this nullable; rows from the Ayla "
        "consumer set this + leave yclients_record_id blank.",
    )
    visit_at = models.DateTimeField(
        help_text="When the actual salon visit is scheduled.",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    scheduled_at = models.DateTimeField(
        help_text="When this reminder should fire (visit_at minus 24h or 2h depending on `kind`).",
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    master_name = models.CharField(max_length=120, blank=True, default="")
    service_name = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Booking reminder"
        verbose_name_plural = "Booking reminders"
        ordering = ["scheduled_at"]
        # The cornerstone of webhook idempotency. Same pair from a
        # re-delivered YClients event hits update_or_create cleanly.
        unique_together = (("yclients_record_id", "kind"),)
        indexes = [
            # send-due hot path: status + scheduled_at window
            models.Index(fields=["tenant", "status", "scheduled_at"]),
            # per-record lookup on update / delete events
            models.Index(fields=["tenant", "yclients_record_id"]),
            models.Index(fields=["kind", "visit_at"]),
        ]
        # #442 booking consumer — partial unique on the Ayla side is
        # created via raw RunSQL in migration 0012 (CREATE UNIQUE INDEX
        # CONCURRENTLY) so prod doesn't acquire a table lock on a
        # large bookingreminder. Declared as a Postgres index, NOT as
        # a Django UniqueConstraint, on purpose: Django's constraint
        # path runs ALTER TABLE → table-level lock + sync DDL. The
        # index gives the same uniqueness guarantee with non-blocking
        # online creation. See migration
        # 0012_remote_booking_proxy_and_ayla_appointment_id.py.

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.master_name} {self.visit_at:%d.%m %H:%M}"


class PendingBookingAction(models.Model):
    """Short-lived bridge between a destructive tool preview + the customer's tap.

    DRF-841 / Phase 1 / B5 (absorbs B4 / DRF-840). The bot now shows a
    2-button confirmation card before EVERY destructive YClients call
    (``confirm_booking`` / ``cancel_booking`` / ``reschedule_booking``).
    The preview phase persists the proposed action here; tapping ✅ in
    the inline keyboard fires :mod:`apps.bookings.callbacks` which
    looks up this row, validates TTL, and executes the action.

    ### Why a dedicated model vs stashing in Conversation.context

    1. **Survives Celery worker restart** — the bot reads the row from
       Postgres in the callback handler; an in-memory or worker-local
       cache would lose pending actions on rolling deploy.
    2. **Cheap TTL filter** — ``expires_at <= now()`` is a single index
       lookup; a JSON blob on Conversation would require Python-side
       parsing on every callback.
    3. **Opaque token** — the row's UUID pk is threaded through the
       callback payload (``cb:book:confirm:<token>``). A stale message
       clicked an hour later finds either ``None`` (cleaned up) or a
       consumed row, both of which short-circuit cleanly.

    ### Kind vs payload

    ``kind`` is the destructive verb (``confirm`` / ``cancel`` /
    ``reschedule``); ``payload`` is the per-kind argument bundle
    captured at preview time:

    * ``confirm``:    ``{master_id, service_id, slot_datetime,
                       client_phone, client_name, master_name,
                       service_name}``
    * ``cancel``:     ``{record_id, reason}``
    * ``reschedule``: ``{record_id, new_datetime, master_id, service_id,
                       master_name, service_name, expected_version}``

    The dict is the source of truth for the callback handler — it
    re-validates the IDs against fresh YClients data at execute time
    to catch staff/service changes during the 10-minute window.

    ### Idempotency

    ``consumed_at`` is set inside a compare-and-set
    ``filter(consumed_at__isnull=True).update(...)`` SQL statement;
    rowcount-zero means another worker (or a double-tap) already
    consumed it. Same idiom as :class:`BookingReminder` callback
    handling — see ``apps.bookings.callbacks`` module docstring.

    ### GDPR / data retention

    Rows are ephemeral by design — the periodic cleanup task (or a
    nightly DB maintenance pass) deletes consumed + expired rows
    older than 24h. The 10-minute TTL bounds the window; the audit
    log keeps the long-term forensic record.
    """

    class Kind(models.TextChoices):
        CONFIRM = "confirm", "Confirm booking (preview → create)"
        CANCEL = "cancel", "Cancel booking (preview → cancel)"
        RESCHEDULE = "reschedule", "Reschedule booking (preview → cancel+create)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="pending_booking_actions",
        help_text="Owning tenant. CASCADE — pending rows are ephemeral "
        "(10-min TTL), no audit value in keeping after tenant deletion.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.CASCADE,
        related_name="pending_booking_actions",
        help_text="Owner of this pending action. Authorisation check "
        "in the callback handler compares the tapping user's BotUser pk "
        "to this FK — prevents a leaked callback id from being executed "
        "by a different user.",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    payload = models.JSONField(
        default=dict,
        help_text="Per-kind argument bundle captured at preview time. "
        "See model docstring for the shape per kind.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        db_index=True,
        help_text="Tap-after-expiry returns a polite 'too much time' "
        "reply and does NOT execute. 10-minute window per spec.",
    )
    consumed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set inside the consume CAS. Non-null → already "
        "executed (or cancelled by user tap on ❌). A second tap "
        "on the same token short-circuits to 'already handled'.",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Pending booking action"
        verbose_name_plural = "Pending booking actions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "bot_user"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} pending={self.consumed_at is None}"


class RemoteBookingProxy(models.Model):
    """Local mirror of an Ayla djangoproject ``Appointment`` row.

    Per ADR-0009 §Domain ownership matrix, Ayla djangoproject owns the
    canonical booking; bot-platform keeps a thin read-cache for AI
    grounding, reminder scheduling, and RFM/sentiment fan-out.

    ### What lives here

    Identifier (``appointment_id`` from Ayla), schedule windows
    (``start_at`` / ``end_at`` — needed for reminder math and for
    ``booking.rescheduled`` duration preservation), status, source,
    and two opaque ID references (``service_id`` + ``specialist_id``)
    that the AI uses to look up display names via the catalog mirror
    on demand.

    ### What does NOT live here (PII rule §7 of event-contract.md)

    No customer name / phone / email / price. The AI fetches those
    from Ayla REST on demand or from the catalog mirror; storing them
    locally would duplicate PII surface for 90+ days of retention.

    ### Idempotency surface

    ``last_synced_event_id`` records the ULID of the last
    cross-service event that updated this row. The ingest-side
    dedupe table (``IngestDedupe``) is the canonical de-duplicator
    on ``event_id``; this field is for forensic trace + observable
    «which event last touched this proxy» rather than the primary
    idempotency mechanism.

    ``salon_notified_at`` / ``client_notified_at`` are a different kind
    of idempotency: not «did we process this event» but «has the
    announcement for this appointment already been claimed», which must
    survive re-delivery under a *fresh* ``event_id`` and must be
    answerable no matter who wrote the row. Rows that predate the
    columns are claimed by migration ``0018`` so the deploy itself
    cannot announce history. DRF-1069.

    Pattern note: copies the catalog ``_MirrorBase`` shape
    (``TenantScopedManager`` + ``all_tenants`` + ``synced_at``) by
    composition, not inheritance — the base assumes a mysite
    ``IntegerField`` for ``external_id``, ours is a UUID.
    """

    class Status(models.TextChoices):
        """Mirrors event-contract.md §3.1 + §3.4 enum values."""

        CONFIRMED = "confirmed", "Confirmed"
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        TENTATIVE = "tentative", "Tentative"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"
        # AMD-018: approved standalone cross-service event (#13) — client
        # did not show up (Ayla state machine mark_no_show).
        NO_SHOW = "no_show", "No show"

    class Source(models.TextChoices):
        """Mirrors event-contract.md §3.1 source enum."""

        MOBILE_APP = "mobile_app", "Mobile app"
        ADMIN_CONSOLE = "admin_console", "Admin console"
        AUTOMATION = "automation", "Automation"
        YCLIENTS_SYNC = "yclients_sync", "YClients sync"

    appointment_id = models.UUIDField(
        primary_key=True,
        editable=False,
        help_text="Canonical Ayla djangoproject Appointment.id (UUID).",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="remote_booking_proxies",
        help_text="Owning tenant. CASCADE — the proxy is derived data; "
        "if the tenant goes, the local mirror goes too.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="remote_booking_proxies",
        help_text="Channel-side user identity. Nullable: an Ayla "
        "mobile-only customer can have bookings before ever opening "
        "the bot, so the proxy is written orphan and a post_save "
        "signal on BotUser backfills the ref when the user appears "
        "via any channel. CASCADE applies only to the linked case: "
        "if the BotUser is purged (GDPR erasure), linked mirrors go "
        "too — orphan rows are unaffected (no link to cascade through).",
    )

    # ── Schedule window (for reminders + reschedule math) ──────────
    start_at = models.DateTimeField(
        db_index=True,
        help_text="Appointment start, salon-local timezone. Reminder "
        "math (T-24h / T-2h) computes against this.",
    )
    end_at = models.DateTimeField(
        help_text="Appointment end. Round-3 spec §3.3: reschedule "
        "preserves duration via "
        "``new_end_at = new_start_at + (old_end_at - old_start_at)`` — "
        "this field is what the math reads.",
    )

    # ── Status + source (mirrors §3 enums) ─────────────────────────
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        db_index=True,
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        blank=True,
        default="",
        help_text="Where the booking originated. Empty when source isn't "
        "carried in the event (booking.cancelled / .rescheduled / "
        ".completed don't repeat the source).",
    )

    # ── Opaque references (catalog mirror does the name lookup) ────
    service_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Ayla Service.id — looked up via catalog mirror for "
        "the AI to say 'у тебя стрижка'. Nullable because update "
        "events (cancelled / rescheduled / completed) don't repeat "
        "the service reference; the row keeps the value set at "
        "creation time.",
    )
    specialist_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Ayla Master.id — analogous to ``service_id``.",
    )

    # ── Announcement claims (DRF-1069) ─────────────────────────────
    #
    # «Has this appointment already been announced, and to whom» — the
    # ONE durable answer, per side. Before DRF-1069 the consumer
    # inferred it from ``get_or_create``'s ``created`` flag, which is
    # false for every booking made in the bot's own dialog (that path
    # writes this mirror row itself, before Ayla's event arrives) — so
    # the salon was never told about the product's main booking path.
    # See ``apps.eventbus.consumers.booking._claim_announcement``.
    salon_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a handler claimed the one-shot salon "
        "announcement for this appointment (DRF-1030 / DRF-1069). NULL "
        "= nobody on the salon side has been told yet. Set inside the "
        "ingest transaction, before the after-commit send: a rolled-back "
        "ingest releases the claim, a re-delivered event finds it taken. "
        "Records the claim, not delivery — the send itself is "
        "best-effort and may still find no reachable recipient.",
    )
    client_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Same claim, for the client-facing «вы записаны» "
        "confirmation (DRF-1066), taken by whichever handler decides "
        "to send it. Stays NULL for a booking made in the bot's own "
        "dialog: there ``execute_confirm`` already answered in chat, "
        "this channel deliberately says nothing, and the chat-origin "
        "marker — not this column — is what keeps it silent.",
    )

    # ── Audit / observability ──────────────────────────────────────
    last_synced_event_id = models.CharField(
        max_length=36,
        blank=True,
        default="",
        help_text="Cross-service event_id (bare ULID 26 chars or Ayla "
        "uuid4 36 chars, #1058) of the last event that touched this row. "
        "Forensic trace, not the primary idempotency key (that's the "
        "IngestDedupe table on the ingest side).",
    )
    synced_at = models.DateTimeField(
        auto_now=True,
        help_text="When the platform last updated this row.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_applied_appointment_version = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Highest canonical `appointment.rescheduled` DER "
        "`version` applied to this proxy (AYLA-DEC-0022/0036 "
        "version-aware ordering). NULL means no canonical "
        "version-tracked event has been applied yet — proxies created "
        "or updated only via legacy booking.created/booking.rescheduled "
        "never set this field. See "
        "apps.eventbus.consumers.booking.handle_appointment_rescheduled_canonical "
        "for the ordering state machine (bootstrap / skip / apply / gap).",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Remote booking proxy"
        verbose_name_plural = "Remote booking proxies"
        ordering = ["-start_at"]
        indexes = [
            # «Next booking for this user» — hot read path for the AI.
            models.Index(fields=["bot_user", "start_at"]),
            # «Active bookings for tenant analytics».
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self) -> str:
        return f"RemoteBookingProxy[{self.appointment_id} {self.status}]"


class PaymentMirror(models.Model):
    """Read model of a client's payment state per appointment (C7.3).

    Fed by the eventbus consumers — ``booking.confirmed`` carrying a
    ``payment_id`` (the hold signal — C7.3 freezes NO separate
    ``payment.authorized`` in the pilot) and the ``payment.*`` family
    (captured / failed / refunded). Powers the optional ``payment``
    field on customer BookingItem responses. ADR-0009: mirror, never
    the source of truth — on-demand ``GET /internal/payments/{id}/`` is
    the authoritative read when fresher state is needed.

    One row per (tenant, appointment): C7.1 idempotency guarantees one
    active payment per appointment, so the unique key is the natural
    one. Amount follows the unified data contract (§1): Decimal, 2dp.
    """

    class CaptureState(models.TextChoices):
        """Mirror states the pilot's events can deliver (C7.3 subset)."""

        AUTHORIZED = "authorized", "Hold (booking.confirmed)"
        CAPTURED = "captured", "Captured"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        CANCELED = "canceled", "Canceled / released"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="payment_mirrors",
        help_text="Owning tenant. CASCADE — mirror is derived data.",
    )
    appointment_id = models.UUIDField(
        db_index=True,
        help_text="Canonical Ayla Appointment.id the payment belongs to.",
    )
    payment_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="Ayla Payment.id. Null for the hold signal when the "
        "booking.confirmed payload omits it.",
    )
    capture_state = models.CharField(
        max_length=16,
        choices=CaptureState.choices,
        help_text="Last known capture state from the event stream.",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Payment amount (Decimal, 2dp per §1).",
    )
    last_synced_event_id = models.CharField(
        max_length=36,
        blank=True,
        default="",
        help_text="event_id of the last event that touched this row "
        "(forensic trace; IngestDedupe is the primary idempotency).",
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Payment mirror"
        verbose_name_plural = "Payment mirrors"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "appointment_id"],
                name="uq_payment_mirror_tenant_appointment",
            ),
        ]

    def __str__(self) -> str:
        return f"PaymentMirror[{self.appointment_id} {self.capture_state}]"
