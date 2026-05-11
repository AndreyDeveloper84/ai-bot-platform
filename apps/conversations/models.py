"""Conversation domain models (DRF-435 / Sprint 2 / B1).

Ported from `Ayla origin/dev:ai/models.py::Conversation` shape with two
deltas:

1. **FK swap**: `user` (auth User) → `bot_user` (identity.BotUser).
   The platform's identity is channel-scoped (see A1 docstring), not
   auth-scoped. Same DB column semantics, different domain.

2. **Default manager**: `TenantScopedManager` (not Ayla's
   `_ConversationManager`). Ayla's manager only hides soft-deleted rows;
   ours additionally scopes by `current_tenant()`. Soft-delete is
   layered on top via the `is_active=True, deleted_at__isnull=True`
   filter inside `resolve_active_conversation` (B3) — the manager
   alone doesn't hide deleted rows because admin/replay code needs to
   see them via `all_tenants`. The conditional UniqueConstraint
   prevents two active rows per `(bot_user, tenant)` regardless of
   manager.

### State enum — minimal per ADR-0007

`State` ships only `{IDLE, CONSULTING, ESCALATED}` in Sprint 2. The
PHASE0_DESIGN.md §3.2 7-state enum is decomposed across Sprint 3+
(BOOKING_FLOW + AWAITING_CONFIRMATION + HUMAN_HANDOFF) and Sprint 4+
(FOOD_LOGGING) — each lands alongside its writer code via a trivial
`alter_choices` migration. See `docs/adr/ADR-0007-conversation-state-enum.md`.

### Outcome enum

Set only when the conversation is closed (by `close_conversation()` in
B3 or by the Sprint 1 cleanup task scheduled in E3). Empty = open
conversation. Sprint 1 retention pattern reuses this — closed-out
conversations stay in DB until the AuditLog/idempotency retention
sweep eventually purges them.

### Conditional UniqueConstraint

Prevents two parallel webhook turns from creating two active
Conversations for the same `(bot_user, tenant)` pair. Postgres-only
partial unique index. SQLite tests for the constraint **must**
skip-mark via `@pytest.mark.skipif(_on_sqlite())` — the constraint
silently no-ops there. CI runs Postgres → contract proven.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from apps.tenancy.managers import TenantScopedManager


class Conversation(models.Model):
    """A single thread between a BotUser and the platform."""

    class State(models.TextChoices):
        # Per ADR-0007: minimal-first. Add new values alongside the
        # writer code that emits them, not pre-emptively.
        IDLE = "idle", "IDLE"
        CONSULTING = "consulting", "CONSULTING"
        ESCALATED = "escalated", "ESCALATED"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        ABANDONED = "abandoned", "Abandoned"
        REDIRECTED = "redirected", "Redirected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="conversations",
        help_text="Owning tenant. PROTECT — dropping a tenant must not "
        "silently delete its conversation history; it's a billing / "
        "legal incident path.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.CASCADE,
        related_name="conversations",
        help_text="The channel-scoped identity who owns this thread.",
    )
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.IDLE,
        help_text="Lifecycle state. Minimal per ADR-0007; new states "
        "land with their writer code in Sprint 3+.",
    )
    outcome = models.CharField(
        max_length=16,
        choices=Outcome.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="Final outcome — set when the conversation is closed "
        "by close_conversation() or the cleanup sweep. Empty = open.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="False after explicit close() OR mark_deleted(). The "
        "active-uniqueness constraint depends on this flag.",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Soft-delete timestamp. Conversations are forensic "
        "data — never hard-deleted from this table; retention sweeps "
        "(Sprint 1 pattern) eventually cascade-prune messages.",
    )
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Set by record_message() on every insert. Drives the "
        "by-recency admin list and inactivity-cleanup queries.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Default manager scopes to current_tenant(). Use ``all_tenants`` for
    # admin / cleanup / replay code that needs to see across tenants.
    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Conversation"
        verbose_name_plural = "Conversations"
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "is_active", "-last_message_at"]),
            models.Index(fields=["bot_user", "-last_message_at"]),
        ]
        constraints = [
            # B3's resolve_active_conversation contract requires exactly
            # one active row per (bot_user, tenant). Without this, two
            # concurrent webhook turns from the same user race into two
            # active conversations. Postgres-only partial unique — see
            # module docstring for SQLite skip-mark note.
            models.UniqueConstraint(
                fields=["bot_user", "tenant"],
                condition=models.Q(is_active=True, deleted_at__isnull=True),
                name="conversation_one_active_per_bot_user_tenant",
            ),
        ]

    def __str__(self) -> str:
        return f"Conversation[{self.id}]({self.state})"

    def mark_deleted(self) -> None:
        """Soft-delete: flip `is_active=False` + stamp `deleted_at`.

        Used by the 152-ФЗ «delete my data» workflow + the inactivity
        cleanup sweep. The conditional unique constraint allows a
        replacement Conversation for the same `bot_user` to be created
        afterwards — that's the whole point of `deleted_at__isnull=True`
        in the constraint condition.
        """

        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])
