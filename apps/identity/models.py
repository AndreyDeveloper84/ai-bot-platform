"""BotUser model (DRF-433 / Sprint 2 / A1).

Identity for everyone who talks to the platform through a channel —
MAX today, Telegram / WhatsApp / web later. A BotUser is **not** an
authenticated user (Django ``auth.User`` covers admin staff); it's the
canonical thread-anchor for chat history, preferences, consent, and
phone-as-secondary-key cross-channel consolidation (Sprint 3+).

### Key shape decisions

* **Channel-agnostic** — `(channel, channel_user_id)` is the natural key
  per tenant, not the legacy `max_user_id` BigInteger. Forward-compat for
  Sprint 3 channels. If we ever drain prod, the legacy `BotUser.max_user_id`
  is migrated to `channel='max', channel_user_id=str(legacy_max_user_id)`.

* **`channel_user_id` is `CharField`, not int** — Telegram chat IDs are
  large signed ints (often negative for groups), WhatsApp uses opaque
  strings, MAX returns ints; storing as string keeps the column uniform
  across channels at the cost of a few bytes per row.

* **Phone is indexed but NEVER stored in AuditLog raw** — phone is PII
  under 152-ФЗ. The audit pipeline uses `bot_user_id` UUID instead. If
  forensic review needs phone, it joins through `bot_user_id` against
  this table at query time.

* **`chat_id` separate from `channel_user_id`** — in some channels
  (Telegram private DM) they're identical, but in MAX the chat_id is
  the conversation key that outbound `send_message` writes to, and may
  differ from the user identity once group chats land Phase 1+.

* **Default manager = `TenantScopedManager`** — `(channel, channel_user_id)`
  is unique *within a tenant*, not globally. Same Telegram user can sign
  up to two different tenant deployments and that's two BotUsers, by
  design.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class BotUser(models.Model):
    """A channel-scoped identity inside a tenant.

    Resolution rule (A2 / `resolve_or_create_bot_user`): lookup by
    ``(current_tenant, channel, channel_user_id)``; create when missing.
    Phone consolidation across channels is *deferred* to Sprint 3 — with
    a single channel (MAX) there are no cross-channel collisions to
    consolidate, and pre-emptive consolidation logic would be tested
    against itself.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="bot_users",
        help_text="Owning tenant. Same (channel, channel_user_id) under "
        "different tenants → two distinct BotUsers by design.",
    )
    channel = models.CharField(
        max_length=32,
        help_text="Channel slug — 'max', 'telegram', 'whatsapp', 'web'.",
    )
    channel_user_id = models.CharField(
        max_length=128,
        help_text="Stable user identifier within the channel. Stored as "
        "string for forward-compat across channels (Telegram ints, "
        "WhatsApp opaque strings, MAX numeric).",
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        default="",
        db_index=True,
        help_text="E.164-normalised phone. PII — never write raw to "
        "AuditLog payload; reference by bot_user_id UUID instead.",
    )
    chat_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Channel-side chat identifier used by outbound send. "
        "Equal to channel_user_id in private DMs; may differ for groups.",
    )
    display_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Display name reported by the channel itself.",
    )
    client_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Name the client typed when booking / introducing "
        "themselves. May differ from channel display_name.",
    )
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    context = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-user scratch JSON for personalisation flags, "
        "consent timestamps, etc. Avoid raw PII — store IDs.",
    )
    timezone = models.CharField(
        max_length=64,
        default="Europe/Moscow",
        help_text="IANA timezone for time-of-day rendering in messages.",
    )

    # GDPR-style soft delete (Phase 3 / F4). ``deleted_at`` set when the
    # customer requests data deletion via the Mini App profile screen.
    # ``soft_delete_user()`` scrubs PII (client_name/phone/context) at the
    # same time. We keep the row so historical FKs (BookingRequest,
    # Conversation) stay intact for salon reporting + audit.
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the customer requested data deletion. Non-null "
        "means PII has been scrubbed and the user should be invisible to "
        "default queries.",
    )

    # Default manager scopes to current_tenant(). Use ``all_tenants`` for
    # admin / maintenance code that must see every row.
    #
    # Soft-deleted users (``deleted_at__isnull=False``) are NOT filtered
    # at the manager layer — that would silently change semantics of
    # every existing call site. Instead, auth (require_init_data) and
    # profile views filter deleted_at explicitly.
    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Bot user"
        verbose_name_plural = "Bot users"
        ordering = ["-last_seen"]
        unique_together = (("tenant", "channel", "channel_user_id"),)
        indexes = [
            models.Index(fields=["tenant", "-last_seen"]),
        ]

    def __str__(self) -> str:
        label = self.display_name or self.client_name or self.channel_user_id
        return f"BotUser[{self.channel}:{label}]"


class UserPreferences(models.Model):
    """Customer-set notification + personal preferences (Phase 3 / F4).

    OneToOne with :class:`BotUser` (primary key = bot_user_id, same shape
    as :class:`ClientProfile`). Editable from the Mini App profile screen
    (F4); read by the proactive scheduler before sending reminders /
    retention / promo / birthday templates.

    Per ``docs/design/handoffs/2026-05-18-customer-first-time-handoff.md``
    §12 F4 — 4 toggles + birthday + allergies. Favorites are computed
    elsewhere (top-master-by-bookings) and surfaced read-only on F4.

    ### Why a separate model and not BotUser.context JSONB

    Per Phase 3 design Q1 — preferences are *contractual*: the
    proactive scheduler MUST honor them, retention SLA is measured
    against opt-out timestamps, and analytics needs structured access
    (count tenants by promo opt-in rate). JSONB hides those shapes
    behind dict lookups and won't be migration-safe when v1.1 adds
    new toggles.
    """

    bot_user = models.OneToOneField(
        BotUser,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="preferences",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="user_preferences",
        help_text="Owning tenant. CASCADE — preferences die with the "
        "tenant, no value in keeping them otherwise.",
    )

    # Notification toggles per handoff §12 F4 layout
    notify_reminders = models.BooleanField(
        default=True,
        help_text="T-24h / T-2h / T-15m reminders for confirmed bookings. "
        "Even when False, transactional confirmations still fire — only "
        "soft reminders mute.",
    )
    notify_retention = models.BooleanField(
        default=True,
        help_text="Proactive «время обновить» nudge ~6 weeks after last visit.",
    )
    notify_promo = models.BooleanField(
        default=False,
        help_text="Promo / marketing campaigns. Default OFF — opt-in.",
    )
    notify_birthday = models.BooleanField(
        default=True,
        help_text="Birthday greeting + gift offer. Requires birthday_date to actually trigger.",
    )

    # Personal data per handoff §12 F4 — optional, can be skipped
    birthday_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional birthday for the birthday greeting flow. Year "
        "is preserved for age-conditional offers but never displayed back.",
    )
    allergies = models.TextField(
        blank=True,
        default="",
        help_text="Free-text contraindications / allergies surfaced to the "
        "master before each booking. Read by the booking confirm view.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "User preferences"
        verbose_name_plural = "User preferences"
        indexes = [
            models.Index(fields=["tenant", "notify_promo"]),
            models.Index(fields=["tenant", "notify_birthday", "birthday_date"]),
        ]

    def __str__(self) -> str:
        return f"UserPreferences[{self.bot_user_id}]"


class ClientProfile(models.Model):
    """Computed RFM/LTV/risk/tier snapshot per bot_user (DRF-527 / Sprint 6 / P1).

    Per PHASE0_DESIGN §3.2: source of truth for the values lives in Booking
    facts (Phase 1+), not here — ClientProfile is a refresh-on-write cache
    feeding the orchestrator. All fields are **derived** by services in
    ``apps.identity.services`` (rfm/ltv/churn/tier/recompute), and the row
    is recomputed daily by `recompute_profiles_daily` (P7) + on
    `booking_completed` signal (P8).

    ### Why OneToOne(primary_key=True)

    A user has one profile, always. Using the BotUser FK as PK keeps the
    table dense (no extra UUID) and makes the reverse relation
    discoverable as ``bot_user.client_profile``.

    ### tenant FK PROTECT — same rationale as ReplayTrace (Sprint 5)

    Profiles are derived state but expensive to recompute (they embed
    historical aggregates). Accidental tenant DROP must not nuke them.
    PROTECT forces the operator to delete profiles explicitly first.
    """

    bot_user = models.OneToOneField(
        BotUser,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="client_profile",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="client_profiles",
        help_text="Owning tenant. PROTECT — profile aggregates are derived "
        "but expensive to recompute; accidental tenant drop must not vapourise them.",
    )

    # --- RFM ---
    recency_days = models.IntegerField(
        null=True,
        blank=True,
        help_text="Days since last visit. NULL when no visits yet.",
    )
    frequency_visits = models.IntegerField(default=0)
    monetary_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Sum of all visit amounts (lifetime).",
    )
    rfm_segment = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="champion | loyal | at_risk | hibernating | new (or empty pre-compute).",
    )

    # --- LTV ---
    ltv = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Lifetime value to date. Alias for monetary_total in Phase 0; "
        "kept separate so Phase 1 LTV models can diverge from raw sum.",
    )
    predicted_ltv_12m = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Predicted LTV over next 12 months. Phase 0 linear; Phase 1 ML.",
    )

    # --- Risk ---
    churn_risk = models.FloatField(
        default=0,
        help_text="0..1 churn score. Phase 0 heuristic from recency / avg_visit_interval; Phase 1 ML.",
    )
    lifecycle_stage = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="new | active | lapsing | churned (or empty pre-compute).",
    )

    # --- Behavior ---
    avg_visit_interval_days = models.IntegerField(
        null=True,
        blank=True,
        help_text="Average days between visits. NULL with <2 visits.",
    )
    favorite_service_id = models.CharField(max_length=64, blank=True, default="")
    favorite_category_id = models.CharField(max_length=64, blank=True, default="")
    preferred_master_id = models.CharField(max_length=64, blank=True, default="")

    # --- Loyalty ---
    loyalty_tier = models.CharField(
        max_length=16,
        default="bronze",
        help_text="bronze | silver | gold | platinum.",
    )

    # --- Bookkeeping ---
    last_recomputed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When recompute_profile last ran. NULL before first compute.",
    )

    # Default manager scopes to current_tenant(); use ``all_tenants`` for admin.
    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Client profile"
        verbose_name_plural = "Client profiles"
        indexes = [
            models.Index(fields=["tenant", "rfm_segment"]),
            models.Index(fields=["tenant", "lifecycle_stage"]),
            models.Index(fields=["tenant", "loyalty_tier"]),
        ]

    def __str__(self) -> str:
        return (
            f"ClientProfile[{self.bot_user_id} "
            f"seg={self.rfm_segment or '—'} tier={self.loyalty_tier}]"
        )
