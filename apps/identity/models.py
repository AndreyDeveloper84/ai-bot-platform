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
from django_cryptography.fields import encrypt

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

    # Bridge to the canonical Ayla User UUID (Ayla djangoproject `users.User.id`).
    # NULL until the user has been linked — typically populated when a
    # `booking.created` event arrives via the cross-service event contract
    # (Gamma's #442 booking consumer) and we observe envelope.user_id for
    # the first time.
    #
    # NOT a Django FK because the canonical User lives in Ayla djangoproject
    # per ADR-0009 §Hard rule #1 (no duplicate canonical state; no shared DB).
    #
    # No unique constraint at this stage: a single Ayla User may have BotUsers
    # in multiple channels (MAX, future Telegram, etc.) or multiple tenants.
    # Lookup pattern: `BotUser.all_tenants.filter(tenant=t, ayla_user_id=u).first()`.
    ayla_user_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Canonical Ayla User UUID (from Ayla djangoproject "
        "users.User.id). NULL until the bridge is established — typically "
        "populated when the booking event consumer (Gamma #442) sees this "
        "channel-user for the first time. Used by the consumer to resolve "
        "envelope.user_id → BotUser for BookingReminder + Conversation update.",
    )

    # Synced from Ayla's `user.profile.updated` domain event (Gamma #446,
    # event-contract.md §3.12). Mirror-only — Ayla owns the canonical
    # value per ADR-0009 §Hard rule #1. Refresh via REST GET
    # /api/v1/users/{ayla_user_id} on event receipt OR re-sync from event
    # payload. Used by bot-platform UI surfaces (mini app, conversation
    # thread, master-side internal-chat) for visual rendering only.
    # Empty string default for backward compat with rows that pre-date
    # the bridge / for users with no avatar set in Ayla.
    avatar_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Avatar URL mirrored from Ayla user.profile.updated event "
        "(per event-contract.md §3.12). Used by bot-platform for UI "
        "rendering (mini app, conversation thread). NOT a canonical "
        "store — Ayla djangoproject is. Refresh via REST GET "
        "/api/v1/users/{ayla_user_id} on event receipt. Empty string "
        "default for backward compat.",
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

    # --- Reviews (Gamma #446 review.created consumer handoff) ---
    # Synced from Ayla's `review.created` domain event per event-contract.md
    # §3.13. Mirror-only — Ayla djangoproject owns the canonical Review
    # state. These fields are the bot-platform's denormalised projection
    # for admin queue / sentiment analytics / proactive nudge logic.
    last_review_rating = models.IntegerField(
        null=True,
        blank=True,
        help_text="Rating (1-5) of this client's most recent review, "
        "from review.created event payload. NULL = no reviews yet.",
    )
    last_review_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the most recent review.created event "
        "was emitted (Ayla domain). NULL = no reviews yet.",
    )
    low_rating_flag = models.BooleanField(
        default=False,
        help_text="True if the most recent review had rating <= 2. "
        "Triggers admin queue «review attention» surfacing.",
    )
    sentiment_score = models.FloatField(
        default=0.0,
        help_text="Derived from last_review_rating: 5→1.0, 4→0.5, 3→0.0, "
        "2→-0.5, 1→-1.0. Bonus field for future analytics. Stays 0.0 "
        "for clients with no reviews yet.",
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


# ─── Sprint 1 Track A — UserPersonalContext memory layer ────────────────
#
# Source of truth: docs/specs/memory-entry-schema.md (canonical tabular spec).
# Companion rationale: docs/adr/ADR-0011-user-personal-context-privacy.md.
# Acceptance criteria: issues #570 (RedZoneReader audit-before-read atomic),
# #571 (RedZoneAccessLog.accessor_principal), #572 (admin red-zone runtime
# assertion).
#
# These models are CROSS-TENANT (no `tenant` FK + no TenantScopedManager).
# UserPersonalContext follows the user across all tenants — that's per
# ADR-0009 §Memory model «core user memory is user-owned, not cross-tenant
# at the storage layer; reuse boundary enforced at app layer via voice
# modulator + zone semantics».
#
# Tenant relationship that a MemoryEntry was sourced FROM is captured by
# the nullable MemoryEntry.source_tenant_id field — informational, not a
# scoping boundary.


class UserPersonalContext(models.Model):
    """Cross-channel, cross-tenant AI memory profile per Ayla User.

    One row per user, ever. Soft-delete only (152-ФЗ erasure right uses
    `soft_deleted_at` + tombstone retention; hard-delete is forbidden).

    See spec `docs/specs/memory-entry-schema.md` §1 for canonical schema.
    """

    user_id = models.UUIDField(
        primary_key=True,
        editable=False,
        help_text="Canonical Ayla User.id. 1-to-1 with UPC. NOT a Django "
        "FK because the canonical User lives in Ayla djangoproject per "
        "ADR-0009 §Hard rule #1 (no duplicate canonical state).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    soft_deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Set when user invoked forget-all OR account-closure. "
        "Hard-delete forbidden — soft-delete + tombstone retention per "
        "152-ФЗ Chapter 3 right-to-be-forgotten implementation.",
    )

    display_name_preferred = models.TextField(
        null=True,
        blank=True,
        help_text="Optional — Ayla's preferred display name for the user.",
    )
    language_preferred = models.CharField(
        max_length=8,
        null=True,
        blank=True,
        help_text="ISO-639-1 language code, e.g. 'ru'. NULL until user sets a preference.",
    )
    summary = models.TextField(
        null=True,
        blank=True,
        help_text="Ayla's running summary of who this user is. "
        "Application-side capped at 8 KB. NOT encrypted at storage layer "
        "because it's intentionally retrievable in plaintext by the LLM "
        "context-building path on every conversation.",
    )

    forget_all_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when user invokes POST /api/v1/users/me/memory/"
        "forget-all (per ADR-0011 §3.3). Records user-intent moment; "
        "async sweep then soft-deletes all entries.",
    )
    minor_lock = models.BooleanField(
        default=False,
        help_text="Per ADR-0011 §10.2 + spec §1: set true when "
        "reconciliation job detects post-fact that the user is a minor; "
        "blocks future yellow/red writes via the writer guard. "
        "Reconciliation job tracked in issue #597.",
    )

    # NOT TenantScopedManager — UPC is cross-tenant by design.
    objects = models.Manager()

    class Meta:
        verbose_name = "User personal context"
        verbose_name_plural = "User personal contexts"
        indexes = [
            models.Index(fields=["soft_deleted_at"], name="upc_soft_del_idx"),
        ]

    def __str__(self) -> str:
        return f"UserPersonalContext[{self.user_id}]"


class MemoryEntry(models.Model):
    """Zone-tagged fact about a user, owned by a UserPersonalContext.

    See spec `docs/specs/memory-entry-schema.md` §2 for canonical schema.

    Sensitivity zones (§5):
      - green:  innocuous (preferences); no consent record required;
                no auto-TTL; encrypted at rest like everything else.
      - yellow: personal (family context, finance signals); CHECK 2
                requires `consent_at IS NOT NULL` at write OR
                soft-delete tombstone; 365-day TTL from last use.
      - red:    sensitive (152-ФЗ §10 special category — health, etc.);
                same consent requirement as yellow; 90-day TTL; reads
                MUST go through RedZoneReader accessor (§10 spec);
                every read writes RedZoneAccessLog row.

    DB-level invariants enforced via 3 CHECK constraints (see spec §4 +
    migration 0005_user_personal_context).

    Direct reads of red-zone rows from production code OUTSIDE
    `apps/identity/services/red_zone_reader.py` are PROHIBITED — both
    by the BEFORE SELECT trigger (DB layer) AND by the AST-based lint
    rule `tools/lint/red_zone_guard.py` (CI layer).
    """

    SENSITIVITY_GREEN = "green"
    SENSITIVITY_YELLOW = "yellow"
    SENSITIVITY_RED = "red"
    SENSITIVITY_ZONE_CHOICES = [
        (SENSITIVITY_GREEN, "Green — innocuous"),
        (SENSITIVITY_YELLOW, "Yellow — personal"),
        (SENSITIVITY_RED, "Red — sensitive (152-ФЗ §10 category)"),
    ]

    SOURCE_EXPLICIT = "explicit"
    SOURCE_INFERRED = "inferred"
    SOURCE_SIGNAL = "signal"
    SOURCE_CHOICES = [
        (SOURCE_EXPLICIT, "Explicit — user stated directly"),
        (SOURCE_INFERRED, "Inferred — Ayla derived from conversation"),
        (SOURCE_SIGNAL, "Signal — derived from observable events"),
    ]

    KIND_CHOICES = [
        ("preference", "Preference"),
        ("contraindication", "Contraindication"),
        ("symptom", "Symptom"),
        ("lifestyle", "Lifestyle"),
        ("relationship", "Relationship"),
        ("financial", "Financial"),
        ("other", "Other"),
    ]

    DELETION_REASON_USER_DELETE = "user_delete"
    DELETION_REASON_WITHDRAWAL = "withdrawal"
    DELETION_REASON_FORGET_ALL = "forget_all"
    DELETION_REASON_TTL_PURGE = "ttl_purge"
    DELETION_REASON_MINOR_PROTECTION = "minor_protection"
    DELETION_REASON_UNKNOWN_LEGACY = "unknown_legacy"
    DELETION_REASON_CHOICES = [
        (DELETION_REASON_USER_DELETE, "User-initiated per-entry delete"),
        (DELETION_REASON_WITHDRAWAL, "Consent withdrawn for yellow/red entry"),
        (DELETION_REASON_FORGET_ALL, "User invoked forget-all"),
        (DELETION_REASON_TTL_PURGE, "Auto-purged by TTL sweep"),
        (DELETION_REASON_MINOR_PROTECTION, "Auto-purged by minor-protection guard"),
        (DELETION_REASON_UNKNOWN_LEGACY, "Pre-spec-era soft-delete (backfill only)"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(
        db_index=True,
        help_text="Denormalised — matches personal_context.user_id. "
        "Convenience for queries that don't go through the UPC join.",
    )
    personal_context = models.ForeignKey(
        UserPersonalContext,
        on_delete=models.CASCADE,
        related_name="memory_entries",
        help_text="Owning UPC. CASCADE — deleting UPC soft-deletes all "
        "entries (semantically: forget-all is a UPC-level + entries "
        "cascade soft-delete; physical CASCADE only relevant for the "
        "rare ops-level hard-delete after 30d tombstone retention).",
    )
    sensitivity_zone = models.CharField(
        max_length=8,
        choices=SENSITIVITY_ZONE_CHOICES,
        db_index=True,
        help_text="Per-fact zone (green/yellow/red). Drives consent "
        "requirement, retention, audit-log scope. See spec §5.",
    )
    source = models.CharField(
        max_length=10,
        choices=SOURCE_CHOICES,
        db_index=True,
        help_text="How the fact entered memory. CHECK 1 enforces "
        "last_inferred_at nullness invariant against source.",
    )
    last_inferred_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When inference last updated this entry. MUST be NULL "
        "when source='explicit' and NOT NULL when source IN ('inferred', "
        "'signal') — enforced by CHECK 1.",
    )
    source_tenant_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="Tenant the fact originated at. NULL if cross-tenant "
        "or platform-level. Informational — NOT a scoping boundary; "
        "tenant scoping is enforced at the app-layer voice modulator + "
        "cross-tenant reuse rule per ADR-0011 §9.",
    )
    kind = models.CharField(
        max_length=20,
        choices=KIND_CHOICES,
        default="other",
        help_text="Categorical bucket for UX (Bonuses tab grouping).",
    )
    content = encrypt(
        models.JSONField(
            default=dict,
            help_text="The fact payload. Encrypted at rest with the Fernet "
            "key via django-cryptography-django5 EncryptedJSONField (per "
            "ADR-0006). Red entries additionally hash-pepper'd before "
            "encryption — pepper management tracked in ADR-0011 §13.5.",
        )
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Updated by the LLM access path (event-sourced batch, "
        "not synchronous — see spec §13.2 yellow-zone TTL sweep + "
        "ADR-0011 §6).",
    )
    last_used_count = models.IntegerField(
        default=0,
        help_text="Rolled up by the yellow-zone access count rollup job.",
    )
    ttl_days = models.IntegerField(
        null=True,
        blank=True,
        help_text="Per-zone retention cap. NULL = no auto-TTL (green "
        "default); 365 (yellow default); 90 (red default).",
    )
    consent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When user explicitly consented to storing this entry. "
        "MUST be NOT NULL for sensitivity_zone IN ('yellow', 'red') "
        "before the entry is read or used — enforced by CHECK 2 + "
        "app-layer write guard. Withdrawal is NOT modeled as setting "
        "consent_at=NULL on a live row; see spec §4 for the soft-delete "
        "exemption pattern.",
    )
    delete_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="User-intent moment for entry deletion. Set in the "
        "same UPDATE as deletion_reason.",
    )
    soft_deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set by the soft-delete job OR in the same transaction "
        "as delete_requested_at during withdrawal. Physical purge happens "
        "30 days later (tombstone retention per spec §5).",
    )
    deletion_reason = models.CharField(
        max_length=20,
        choices=DELETION_REASON_CHOICES,
        null=True,
        blank=True,
        help_text="Distinguishes WHY a row is being deleted. CHECK 3 "
        "enforces nullness invariant: live rows MUST have NULL; deleted "
        "rows MUST have non-NULL. unknown_legacy reserved for backfill.",
    )

    # NOT TenantScopedManager — MemoryEntry is cross-tenant by design.
    objects = models.Manager()

    class Meta:
        verbose_name = "Memory entry"
        verbose_name_plural = "Memory entries"
        indexes = [
            models.Index(fields=["user_id", "sensitivity_zone"], name="me_user_zone_idx"),
            models.Index(
                fields=["sensitivity_zone", "last_used_at"],
                name="me_zone_ttl_idx",
                condition=models.Q(soft_deleted_at__isnull=True),
            ),
            models.Index(
                fields=["delete_requested_at"],
                name="me_delete_req_idx",
                condition=models.Q(
                    delete_requested_at__isnull=False,
                    soft_deleted_at__isnull=True,
                ),
            ),
        ]
        # NOTE: 3 DB CHECK constraints are managed by migration 0007 via
        # RunSQL with NOT VALID + VALIDATE pattern (spec §4 + §12). We do
        # NOT use models.CheckConstraint here because:
        # 1. Django CheckConstraint adds the constraint in CreateModel
        #    inside one transaction → defeats the NOT VALID + VALIDATE
        #    split (the whole point is to allow the long-running VALIDATE
        #    step to run in a separate transaction on a populated table).
        # 2. Future schema-tightening migrations on populated production
        #    tables would have the same problem; standardize on RunSQL.
        # See migration 0007_user_personal_context for the actual SQL.
        # Application-side validation lives in apps/identity/services/
        # memory_writer.py (veha 3).

    def __str__(self) -> str:
        return (
            f"MemoryEntry[{self.id} zone={self.sensitivity_zone} "
            f"kind={self.kind} user={self.user_id}]"
        )


class RedZoneAccessLog(models.Model):
    """Immutable audit log for red-zone reads + writes + purges.

    See spec `docs/specs/memory-entry-schema.md` §3 for canonical schema.

    INSERT-only — enforced at DB level via dedicated role + trigger blocking
    UPDATE/DELETE on this table. 7-year retention.

    No FK CASCADE on memory_entry_id — log rows STAY when MemoryEntry is
    physically purged. The 152-ФЗ Chapter 3 «who accessed» auditor query
    depends on log surviving entry purge.

    Per round-2 AS2 fix: `accessor_principal` carries concrete identity
    (Celery worker hostname / cron name / staff UUID / service-account
    name) beyond the coarse `accessor_role` enum. Indexed for «every
    access by staff X in date range» queries.
    """

    ACCESSOR_AYLA_LLM = "ayla_llm"
    ACCESSOR_SYSTEM_JOB = "system_job"
    ACCESSOR_OPS_ADMIN = "ops_admin"
    ACCESSOR_ROLE_CHOICES = [
        (ACCESSOR_AYLA_LLM, "Ayla LLM prompt construction"),
        (ACCESSOR_SYSTEM_JOB, "System job (TTL sweep, forget-all)"),
        (ACCESSOR_OPS_ADMIN, "Ops admin (break-glass)"),
    ]

    ACCESS_READ = "read"
    ACCESS_WRITE = "write"
    ACCESS_PURGE = "purge"
    ACCESS_WITHDRAWAL = "withdrawal"
    ACCESS_WRITE_REJECTED_DOB = "write_rejected_dob_lookup"
    ACCESS_TYPE_CHOICES = [
        (ACCESS_READ, "Read"),
        (ACCESS_WRITE, "Write"),
        (ACCESS_PURGE, "Purge"),
        (ACCESS_WITHDRAWAL, "Withdrawal — explicit consent revocation"),
        (
            ACCESS_WRITE_REJECTED_DOB,
            "Write rejected — DOB lookup failed (Ayla REST outage)",
        ),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    memory_entry_id = models.UUIDField(
        help_text="The entry being accessed. NOT a Django FK with CASCADE "
        "— log rows persist after entry purge. Migration uses RunSQL to "
        "add raw FK with ON DELETE NO ACTION.",
    )
    user_id = models.UUIDField(
        help_text="Subject the entry belongs to. Indexed via "
        "(user_id, ts) for the primary auditor query.",
    )
    accessor_role = models.CharField(
        max_length=20,
        choices=ACCESSOR_ROLE_CHOICES,
        help_text="Coarse category. Round-2 AS2: insufficient alone for "
        "152-ФЗ Chapter 3 «who accessed» — accessor_principal carries "
        "the concrete identity.",
    )
    accessor_principal = models.CharField(
        max_length=256,
        help_text="Round-2 AS2 fix — concrete identity per accessor_role: "
        "Celery worker hostname + queue name (ayla_llm); cron job name + "
        "worker hostname (system_job); staff User.id UUID string "
        "(ops_admin); caller service-account name (future service_to_"
        "service). Indexed for staff-X-date-range auditor queries.",
    )
    access_type = models.CharField(
        max_length=32,
        choices=ACCESS_TYPE_CHOICES,
        help_text="What kind of access. Round-2 AS2 + ADR-0011 §11.3 "
        "added 'withdrawal' + 'write_rejected_dob_lookup' values.",
    )
    ts = models.DateTimeField(
        auto_now_add=True,
        help_text="When the access happened. Indexed via three composite indexes per spec §3.",
    )
    request_id = models.UUIDField(
        help_text="Audit reference — Celery task id, HTTP request id, OR "
        "ops ticket id. Validated as UUID by the BEFORE SELECT trigger "
        "(spec §9). Round-3 A5: MUST be a real audit reference, not "
        "arbitrary string.",
    )
    purpose = models.TextField(
        help_text="Human-readable purpose (round-3 A5): "
        "'contraindication_check', 'subject_access_request', "
        "'incident_debug', 'ttl_sweep', etc.",
    )

    objects = models.Manager()

    class Meta:
        verbose_name = "Red-zone access log"
        verbose_name_plural = "Red-zone access logs"
        indexes = [
            # Primary auditor query: every access to user X in date range.
            models.Index(fields=["user_id", "ts"], name="rzlog_user_ts_idx"),
            # Secondary auditor query: every access by staff X (round-2 AS2).
            models.Index(
                fields=["accessor_principal", "ts"],
                name="rzlog_principal_ts_idx",
            ),
            # Operational: withdrawals/DOB-failures in date range.
            models.Index(fields=["access_type", "ts"], name="rzlog_type_ts_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"RedZoneAccessLog[{self.access_type} entry={self.memory_entry_id} "
            f"user={self.user_id} ts={self.ts:%Y-%m-%d %H:%M:%S}]"
        )
