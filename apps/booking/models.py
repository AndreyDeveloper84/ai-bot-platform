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
      goes through :class:`BookingReminder.Status.CANCELLED` on the
      linked reminder rows instead.

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
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Booking request"
        verbose_name_plural = "Booking requests"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "bot_user"]),
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
        blank=True,
        help_text="YClients record id (stringified — YClients ints fit "
        "fine but the API will return larger opaque ids on enterprise "
        "tenants per their roadmap).",
    )
    chat_id = models.CharField(
        max_length=128,
        help_text="Snapshot of BotUser.chat_id at write time. "
        "Snapshot — chat_id may change in BotUser later.",
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
        help_text="When this reminder should fire (visit_at minus 24h "
        "or 2h depending on `kind`).",
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

    def __str__(self) -> str:
        return (
            f"{self.get_kind_display()} {self.master_name} "
            f"{self.visit_at:%d.%m %H:%M}"
        )
