"""Loyalty (Volna 4) — points-tracking persistence.

Phase 1.a (this PR): the minimal data layer that lets
:class:`apps.loyalty.subscribers.LoyaltySubscriber` consume
``booking.completed`` events and credit points without touching any
existing booking/checkout code. Tiers, referrals, redemption UI,
config-per-tenant — all deferred to subsequent PRs.

### Two models

- :class:`LoyaltyAccount` — current state per (tenant, customer): balance,
  enrollment status, opt-out flag. One row per customer per tenant.
- :class:`LoyaltyEvent` — append-only history of every balance change.
  The audit trail; balance reconstruction relies on it.

### Why an event log alongside balance

Loyalty disputes always come down to «откуда взялись эти баллы».
Storing only the running balance makes that question unanswerable
(and the support load will be 5x higher per the existing salon
incidents documented in the handoff). The event log is the
forensically defensible source of truth; balance is a denormalised
cache for fast reads.

### Idempotency

``LoyaltyEvent.booking`` + ``LoyaltyEvent.event_type`` uniqueness
prevents double-earning when the eventbus dispatcher retries a
booking.completed envelope. The subscriber attempts an INSERT with
``EarnVisit + booking_id``; the unique constraint vetoes a duplicate
silently.

### Tenant scoping

Both models carry an explicit ``tenant`` FK with
:class:`TenantScopedManager` as the default manager. Cross-tenant
reads (cleanup, replay, the subscriber's own lookup before crediting)
go through ``all_tenants``.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class LoyaltyAccount(models.Model):
    """One row per (tenant, customer). Current loyalty state.

    Created lazily by :func:`apps.loyalty.services.get_or_create_account`
    on first earning event. Account creation is cheap — no migration
    work needed for new customers; the loyalty subscriber upserts.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="loyalty_accounts",
        help_text="Owning tenant. PROTECT — loyalty rows are billing-"
        "adjacent forensic artifacts; explicit purge before tenant deletion.",
    )
    customer = models.OneToOneField(
        "identity.BotUser",
        on_delete=models.CASCADE,
        related_name="loyalty_account",
        help_text="The customer this account belongs to. CASCADE because "
        "loyalty data follows the customer's lifecycle (OP6 erasure deletes "
        "the BotUser → account goes with it; AuditLog keeps the trail).",
    )
    balance = models.IntegerField(
        default=0,
        help_text="Current redeemable points. Always derivable from "
        "LoyaltyEvent sum; cached here for O(1) reads at every booking "
        "checkout. Reconciliation cron (future) verifies the cache.",
    )

    class Tier(models.TextChoices):
        """Loyalty tier per handoff §3.

        Derived from completed-visit count (Phase 2.a) and LTV (deferred).
        Persisted here as a denormalised cache; the LoyaltyEvent log is
        the source of truth (each TIER_CHANGED row records the
        transition).
        """

        STARTER = "starter", "Стартовый"
        REGULAR = "regular", "Постоянный"
        FAVORITE = "favorite", "Любимый"

    tier = models.CharField(
        max_length=16,
        choices=Tier.choices,
        default=Tier.STARTER,
        db_index=True,
        help_text="Current loyalty tier per handoff §3. Recomputed after "
        "every EARN_VISIT and REFUND_REVOKE via "
        "apps.loyalty.services.recompute_tier. Stamps tier_changed_at "
        "and writes a TIER_CHANGED LoyaltyEvent + emits "
        "customer.tier.changed envelope only when the value differs.",
    )
    tier_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the account last transitioned tier. NULL on "
        "accounts that have always been Стартовый.",
    )
    enrolled = models.BooleanField(
        default=True,
        help_text="Per Q-L5 — automatic enrollment on first earning event. "
        "Customer can opt out via profile preferences (Q-L12); set False + "
        "stamp opted_out_at. Existing balance retained but no new earnings.",
    )
    opted_out_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when customer disables loyalty in profile preferences. "
        "Subscriber checks this before crediting (Q-L12 — retain balance, "
        "stop accrual, allow redemption).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Loyalty account"
        verbose_name_plural = "Loyalty accounts"
        ordering = ["-updated_at"]
        indexes = [
            # Owner panel / analytics: top customers by balance per tenant.
            models.Index(fields=["tenant", "-balance"]),
        ]

    def __str__(self) -> str:
        return f"LoyaltyAccount[{self.customer_id} balance={self.balance}]"


class LoyaltyEvent(models.Model):
    """Append-only history of every balance change.

    Subscriber and service-layer code always write here; never directly
    update :attr:`LoyaltyAccount.balance` without a corresponding event
    row. The balance field is a derived cache.

    ### Type enum

    Mirrors the handoff §12 «Event-driven processor» list. Adding a new
    type = one row in the choices tuple + migration; calling code reads
    the constants.
    """

    class EventType(models.TextChoices):
        EARN_VISIT = "earn_visit", "Earn — visit completed"
        EARN_REFERRAL = "earn_referral", "Earn — referral converted"
        EARN_BIRTHDAY = "earn_birthday", "Earn — birthday bonus"
        EARN_REVIEW = "earn_review", "Earn — review left"
        EARN_RETURN = "earn_return", "Earn — long-gap return"
        REDEEM = "redeem", "Redeem — discount applied"
        MANUAL_ADJUST = "manual_adjust", "Manual — admin adjustment"
        REFUND_REVOKE = "refund_revoke", "Revoke — booking refunded/cancelled"
        TIER_CHANGED = "tier_changed", "Tier — threshold crossed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="loyalty_events",
    )
    account = models.ForeignKey(
        LoyaltyAccount,
        on_delete=models.CASCADE,
        related_name="events",
        help_text="The account this event mutates. CASCADE follows account "
        "lifecycle (which itself cascades from BotUser per OP6 erasure).",
    )
    event_type = models.CharField(
        max_length=24,
        choices=EventType.choices,
    )
    points_delta = models.IntegerField(
        help_text="Signed delta applied to balance. Positive for earn, "
        "negative for redeem/revoke/manual_adjust(down).",
    )
    balance_after = models.IntegerField(
        help_text="Balance after this event applied. Stored (not computed) "
        "so dispute drilling doesn't have to replay the whole history.",
    )
    reason = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Human-readable reason. Free-form but conventional: "
        '"visit completed (booking <uuid>)", "manual: customer goodwill", '
        '"booking.cancelled cascade".',
    )
    booking = models.ForeignKey(
        "booking.BookingRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_events",
        help_text="Source booking for visit-bound events. SET_NULL — the "
        "event row outlives the booking row when booking is purged (analytics "
        "preserves the earning fact without the underlying booking).",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Implementation tags (correlation_id from envelope, "
        "service price snapshot, calc breakdown). PII-free.",
    )
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Loyalty event"
        verbose_name_plural = "Loyalty events"
        ordering = ["-occurred_at"]
        constraints = [
            # Idempotency: subscriber retries on the same booking.completed
            # envelope MUST NOT double-earn. EARN_VISIT + booking is the
            # natural dedup key; subscriber uses get_or_create on this.
            models.UniqueConstraint(
                fields=["account", "event_type", "booking"],
                condition=models.Q(event_type="earn_visit", booking__isnull=False),
                name="loyalty_earn_visit_unique_per_booking",
            ),
            # Phase 1.b: same idempotency promise for REFUND_REVOKE. A
            # booking.cancelled redelivery from the dispatcher must NOT
            # revoke points twice — the second revoke would double-debit
            # the balance and could push it negative if the customer earned
            # elsewhere meanwhile.
            models.UniqueConstraint(
                fields=["account", "event_type", "booking"],
                condition=models.Q(event_type="refund_revoke", booking__isnull=False),
                name="loyalty_refund_revoke_unique_per_booking",
            ),
        ]
        indexes = [
            # Customer history page: per-account most-recent N events.
            models.Index(fields=["account", "-occurred_at"]),
            # Owner analytics: events of one type in a window per tenant.
            models.Index(fields=["tenant", "event_type", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        sign = "+" if self.points_delta >= 0 else ""
        return f"LoyaltyEvent[{self.event_type} {sign}{self.points_delta} → {self.balance_after}]"
