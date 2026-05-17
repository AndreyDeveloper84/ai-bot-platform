"""Order + PaymentEvent models (DRF-843 / Phase 1 / B7).

Two models — :class:`Order` (the money intent / receipt) and
:class:`PaymentEvent` (an idempotency ledger of upstream webhook
deliveries).

### Why separated from BookingRequest

A salon visit booking and a gift-certificate purchase are different
artefacts:

* A :class:`BookingRequest` reserves a slot on a master's calendar; it
  has a service, a master, a visit_at, and a status that flips through
  the YClients lifecycle. It is settled at the salon, usually via cash
  or terminal payment.
* An :class:`Order` is a money intent: someone wants to pay 5 000 ₽
  for a gift certificate they can later redeem (or hand to a friend).
  It has an amount, a payment provider id, a checkout URL, and a
  status that tracks payment lifecycle.

The two intersect in scenarios like "pay upfront for a 10-session
package" (Phase 2+) — at that point the Order's ``kind`` taxonomy
extends and a ForeignKey to BookingRequest may be added. For now the
intersection is empty: certificates do not block calendar slots.

### Multi-tenant compatibility

Both models include a ``tenant`` FK + :class:`TenantScopedManager`
default manager — same pattern as :class:`apps.booking.models.BookingRequest`.

### Webhook idempotency contract

:attr:`PaymentEvent.external_event_id` carries a unique constraint.
The YooKassa webhook receiver uses ``Model.objects.get_or_create``
on this field as its primary deduplication mechanism — a re-delivered
event (YooKassa retries any non-2xx for hours) hits the unique
constraint, the receiver short-circuits to "already processed", and
no side-effect fires twice.

### Status lifecycle (Order)

::

    pending  ──── client tapped buy_certificate ──────►  awaiting_payment
                  (Order row written, but no YooKassa            │
                   call yet)                                     │
                                                                 ▼
                                                      ┌─── YooKassa
                                                      │    create_payment
                                                      │    returned a
                                                      │    checkout_url
                                                      ▼
                                          paid  ←──── awaiting_payment ───►  cancelled
                                  (webhook fired               │           (user abandoned /
                                   payment.succeeded)          │            payment.canceled)
                                                               ▼
                                                            failed
                                                  (YooKassa create_payment
                                                   raised — Order is
                                                   surfaced for manager
                                                   handoff)

The transitions form a strict DAG: a paid order never moves back to
awaiting_payment; a failed order is terminal (a new order is created
on retry). The webhook fulfilment task enforces this with a
compare-and-set on ``status``.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class Order(models.Model):
    """A payment-bearing artefact (gift certificate today; extensible).

    See module docstring for the lifecycle DAG and the separation
    rationale vs :class:`apps.booking.models.BookingRequest`.
    """

    class Kind(models.TextChoices):
        """What the order is for.

        Only ``CERTIFICATE`` exists in Phase 1. The choice list is
        kept as a TextChoices (not a plain str column) so the next
        kind — ``DEPOSIT`` / ``PACKAGE`` / etc. — lands without a
        column migration.
        """

        CERTIFICATE = "certificate", "Gift certificate"

    class Status(models.TextChoices):
        """Order lifecycle states — see module docstring DAG.

        * ``PENDING``           — row created, no YooKassa call yet.
        * ``AWAITING_PAYMENT``  — YooKassa returned a checkout URL.
        * ``PAID``              — webhook ``payment.succeeded`` flipped it.
        * ``CANCELLED``         — webhook ``payment.canceled`` flipped it.
        * ``FAILED``            — YooKassa create_payment raised.
        """

        PENDING = "pending", "Pending"
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="orders",
        help_text="Owning tenant. PROTECT — orders are a billing / "
        "forensic artefact and must outlive a tenant-delete request "
        "(audit retention requirement). Tenant deletion requires an "
        "explicit data-purge first.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        help_text="Linked channel identity. SET_NULL — orders persist "
        "as a receipt even after a GDPR-erasure of the BotUser.",
    )

    kind = models.CharField(
        max_length=24,
        choices=Kind.choices,
        default=Kind.CERTIFICATE,
        help_text="What the order is for. Only 'certificate' in Phase 1.",
    )
    amount_rub = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Order amount in roubles. Clamped to [500, 100000] at "
        "the tool layer — the database column doesn't enforce the "
        "range (admin-side creation must remain unconstrained for "
        "edge-case fixups).",
    )

    # ── Snapshot fields (denormalised at write time) ──────────────────
    recipient_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Name printed on the certificate. Empty when the client buys for themselves.",
    )
    recipient_email = models.EmailField(
        blank=True,
        default="",
        help_text="Where to email the certificate link. Empty when the "
        "client wants the link delivered in-bot only.",
    )
    buyer_email = models.EmailField(
        blank=True,
        default="",
        help_text="Buyer's email — used by YooKassa for the 54-FZ "
        "receipt (FT-13 plumbing lands in a follow-up).",
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Short Russian description shown to the buyer on the YooKassa checkout page.",
    )

    # ── External provider state ────────────────────────────────────────
    external_payment_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="YooKassa payment.id (UUID). Indexed because the "
        "webhook handler looks orders up by this column.",
    )
    checkout_url = models.TextField(
        blank=True,
        default="",
        help_text="YooKassa-hosted checkout URL. The bot sends the user "
        "here via an inline 'Оплатить' button.",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set by the fulfilment task on the ``payment.succeeded`` webhook.",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "status"]),
            # external_payment_id has its own db_index=True (single-column
            # lookup from the webhook). No tenant prefix — the webhook
            # comes in BEFORE tenant scope is established and a global
            # lookup is correct (the external id is globally unique on
            # the YooKassa side).
        ]

    def __str__(self) -> str:
        return f"Order {self.id} {self.kind} {self.amount_rub}₽ [{self.status}]"


class PaymentEvent(models.Model):
    """Idempotency ledger for inbound payment-provider webhook deliveries.

    YooKassa retries any non-2xx response for hours. The uniqueness
    constraint on :attr:`external_event_id` is the cornerstone of
    idempotency: a re-delivered event hits the constraint, the
    webhook handler short-circuits, and no side-effect (no message
    send, no status flip, no email) fires twice.

    The row is kept indefinitely for audit / reconciliation. A
    cleanup task may sweep ``received_at < now - 90 days AND processed
    = True`` rows in a future ticket; out of scope for B7.

    ### Why FK to Order is nullable

    A webhook for an order we don't recognise (mis-routed delivery,
    stale event from a deleted order) still gets persisted — the row
    is forensic evidence that the upstream sent something. The handler
    just marks ``processed=False`` and moves on.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_event_id = models.CharField(
        max_length=64,
        unique=True,
        help_text="YooKassa event UUID. UNIQUE — re-delivery hits this "
        "constraint and the handler short-circuits.",
    )
    event_type = models.CharField(
        max_length=64,
        help_text="YooKassa event slug — e.g. ``payment.succeeded``, "
        "``payment.canceled``, ``refund.succeeded``.",
    )
    order = models.ForeignKey(
        "Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_events",
        help_text="Linked order. SET_NULL because a webhook for an "
        "unknown order id is still forensic evidence of upstream "
        "behaviour and must persist.",
    )
    raw_payload = models.JSONField(
        default=dict,
        help_text="Full upstream payload. Used for replay / debug. "
        "MUST NOT contain the YooKassa secret key (the webhook body "
        "doesn't include credentials, but a defence-in-depth scrub "
        "at write time is the handler's responsibility).",
    )
    received_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(
        default=False,
        help_text="True once the handler ran the side effect "
        "(status flip + fulfilment enqueue) for this event.",
    )

    class Meta:
        verbose_name = "Payment event"
        verbose_name_plural = "Payment events"
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["-received_at"]),
            models.Index(fields=["event_type"]),
        ]

    def __str__(self) -> str:
        return f"PaymentEvent {self.event_type} {self.external_event_id}"
