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

    # Default manager scopes to current_tenant(). Use ``all_tenants`` for
    # admin / maintenance code that must see every row.
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
