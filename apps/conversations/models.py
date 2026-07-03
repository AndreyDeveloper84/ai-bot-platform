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

    class Tier(models.TextChoices):
        # Per docs/design/policies/conversation-ownership-policy.md §4.
        # AI_CONTINUITY: bot replies autonomously; master can read +
        # send (audited).
        # HUMAN_SUPERVISED: bot drafts only; master/admin reviews
        # before send (out of scope for this PR — placeholder).
        # HUMAN_LOCKED: bot is silent; only admin/owner can speak.
        # Master surface goes read-only (see M6 §M6 layout block).
        AI_CONTINUITY = "ai_continuity", "AI Continuity"
        HUMAN_SUPERVISED = "human_supervised", "Human Supervised"
        HUMAN_LOCKED = "human_locked", "Human Locked"

    class State(models.TextChoices):
        # Per ADR-0007: minimal-first. Add new values alongside the
        # writer code that emits them, not pre-emptively.
        IDLE = "idle", "IDLE"
        CONSULTING = "consulting", "CONSULTING"
        ESCALATED = "escalated", "ESCALATED"
        # Added Sprint 3 / C3 (DRF-466). Set by handoff.services.create_admin_task;
        # cleared back to IDLE by handoff.services.resolve_admin_task. D4 dispatcher
        # short-circuits skill execution when state == HUMAN_HANDOFF.
        HUMAN_HANDOFF = "human_handoff", "HUMAN_HANDOFF"

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
        on_delete=models.PROTECT,
        related_name="conversations",
        help_text="The channel-scoped identity who owns this thread. "
        "PROTECT (Sprint 2.5 H1): a stray `bot_user.delete()` previously "
        "cascaded → Conversation → Message hard-delete, bypassing the "
        "soft-delete invariant + leaving no audit trail. Forces callers "
        "through `apps.identity.services.delete_bot_user_data()` which "
        "soft-deletes conversations first, then deletes the BotUser.",
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
    # Sprint 8 / N3 (DRF-702) — shadow-mode marker.
    # When True the row was produced by a shadow turn (X-Shadow:1 from
    # nginx mirror OR tenant.shadow_mode=True). Shadow rows live alongside
    # the primary Conversation for the same bot_user — the active-
    # uniqueness constraint excludes them so the primary path is not
    # blocked by a parallel shadow turn.
    is_shadow = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True when this row belongs to a shadow-mode turn. "
        "Shadow rows are observability artifacts; outbound is suppressed.",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Soft-delete timestamp. Conversations are forensic "
        "data — never hard-deleted from this table; retention sweeps "
        "(Sprint 1 pattern) eventually cascade-prune messages.",
    )
    # Sprint 9 / D3 (DRF-835) — multi-step skill FSM state.
    #
    # ``apps.skills.fsm.SkillFSM`` serialises its in-flight state here
    # so a multi-step skill (nutrition_anketa, food_correction) can
    # resume across turns + bot restarts. Default empty dict means
    # "no active FSM" — most skills are stateless and never touch it.
    #
    # The key in the dict is the skill name (``"nutrition_anketa"``);
    # the value is the FSM's ``serialize()`` output. This lets two
    # skills coexist (rare, but possible during cross_domain transitions)
    # without colliding state.
    #
    # NOT touched by retention sweeps separately — falls under the
    # conversation row's lifecycle.
    skill_state = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-skill FSM state {skill_name: state_dict}. "
        "Sprint 9 D3 — see apps/skills/fsm.py.",
    )
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Set by record_message() on every insert. Drives the "
        "by-recency admin list and inactivity-cleanup queries.",
    )
    last_booking_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Tenant-local datetime of the customer's most recent confirmed "
            "booking start. Set from booking.confirmed event data.start_at "
            "per docs/architecture/event-contract.md §3.1.4. Used by AI "
            "grounding to anchor «когда вы у нас были последний раз»."
        ),
    )
    # Payment event grounding fields. Populated by Gamma's payment consumer
    # from Ayla canonical payment.* events per event-contract.md §3.5-§3.8.
    # All nullable — pre-existing rows stay NULL until first payment event.
    last_payment_captured_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the customer's most recent successful payment "
            "was captured (Ayla canonical payment.captured event). "
            "Used for AI context grounding."
        ),
    )
    last_payment_failed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the customer's most recent payment attempt failed.",
    )
    last_payment_failure_code = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=(
            "Enum code for last failure reason. Values: "
            "'insufficient_funds' | 'card_expired' | 'card_declined' "
            "| 'three_d_secure_failed' | 'other'. "
            "CRITICAL: enum-only — YooKassa error messages may "
            "contain PII (card numbers). Free-text reasons must be "
            "mapped to enum in payment consumer."
        ),
    )
    last_payment_refunded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the customer's most recent payment was refunded.",
    )
    pending_payment_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Ayla canonical payment_id for currently-pending payment "
            "(authorized but not yet captured/failed). Reset to NULL "
            "on captured/failed/refunded."
        ),
    )
    consecutive_payment_failures = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "Counter incremented on payment.failed, reset to 0 on "
            "payment.captured. Used for human handoff threshold "
            "(PAYMENT_FAILED_HANDOFF_THRESHOLD env var)."
        ),
    )
    last_payment_event_id = models.CharField(
        max_length=36,  # bare ULID 26 or Ayla uuid4 36 (#1058)
        blank=True,
        default="",
        help_text=(
            "Cross-service event_id (bare ULID 26 chars or Ayla uuid4 "
            "36 chars, #1058) of the last payment.* event that touched "
            "this conversation. Handler-level idempotency key: if "
            "envelope.event_id matches this, the handler short-circuits "
            "before side-effects. Mirrors the "
            "RemoteBookingProxy.last_synced_event_id pattern from "
            "#442. Forensic trace, NOT the primary idempotency "
            "guard — that's IngestDedupe at the dispatcher layer."
        ),
    )
    # Master M6 / PR M6.1 — conversation ownership tier per
    # docs/design/policies/conversation-ownership-policy.md §4.
    tier = models.CharField(
        max_length=24,
        choices=Tier.choices,
        default=Tier.AI_CONTINUITY,
        db_index=True,
        help_text=(
            "Ownership tier. HUMAN_LOCKED disables master compose + AI "
            "auto-reply; only admin/owner downgrades."
        ),
    )
    tier_reason_class = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=(
            "Classification when tier is locked: "
            "complaint|financial|medical|other. Empty otherwise."
        ),
    )
    tier_locked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the conversation entered HUMAN_LOCKED.",
    )
    tier_locked_by_master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations_locked",
        help_text="Master who promoted the conversation to HUMAN_LOCKED.",
    )
    tier_locked_reason_text = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Free-form forensic context. Never surfaced as PII.",
    )
    last_read_by_master_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Last time the assigned master tapped mark-read on this "
            "conversation. Used to compute the per-master unread count."
        ),
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
            # Sprint 8 / N3 — shadow-mode investigation queries.
            models.Index(fields=["tenant", "is_shadow", "-created_at"]),
        ]
        constraints = [
            # B3's resolve_active_conversation contract requires exactly
            # one active row per (bot_user, tenant). Without this, two
            # concurrent webhook turns from the same user race into two
            # active conversations. Postgres-only partial unique — see
            # module docstring for SQLite skip-mark note.
            #
            # Sprint 8 / N3: the partial filter now also requires
            # `is_shadow=False` so a shadow turn for the same bot_user
            # can hold its own row in parallel with the primary active.
            models.UniqueConstraint(
                fields=["bot_user", "tenant"],
                condition=models.Q(is_active=True, deleted_at__isnull=True, is_shadow=False),
                name="conversation_one_active_per_bot_user_tenant",
            ),
            # Conversations retro Y6: ``state`` is a CharField with
            # TextChoices, which Django does NOT enforce at the DB
            # layer. A hand-edited row with ``state='zombie'`` would
            # load silently and the D4 dispatcher's HUMAN_HANDOFF
            # short-circuit would pass through → bot runs on undefined
            # state. CHECK constraint keeps the model and DB invariants
            # aligned. New states must be added here AND to the State
            # enum (linkage enforced via the migration that adds them).
            models.CheckConstraint(
                condition=models.Q(state__in=["idle", "consulting", "escalated", "human_handoff"]),
                name="conversation_state_known_value",
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


class Message(models.Model):
    """A single turn inside a Conversation (DRF-452 / Sprint 2 / B2).

    Persists every turn — user, assistant, tool, system. Ported from
    `Ayla origin/dev:ai/models.py::Message` shape with two additions
    per PHASE0_DESIGN.md §3.3:

    1. **`rendered_text`** — the canonical text the client actually saw
       after channel-specific rendering (markdown stripped, buttons
       converted to inline text on channels without button UI, etc.).
       The `content` field is the source-of-truth from the LLM /
       handler; `rendered_text` is what the user can quote back to
       support staff.

    2. **`trace_id`** — propagates from `current_trace_id()` via B3's
       `record_message()`. Links this message to its WebhookJournal
       row, Redis Stream entry, AuditLog rows, and Events. Sprint 5
       replay reconstructs the per-turn pipeline by indexing on this.

    ### Denormalised `tenant` FK

    `Message.tenant` mirrors `Message.conversation.tenant` on every
    insert. Two reasons:

    1. The cross-tenant leakage scanner (E1 from Sprint 1) operates
       per-model via `TenantScopedManager`; a model without a direct
       `tenant` FK doesn't participate in the scanner. Putting `tenant`
       on Message gives us the same guarantee here as on Conversation.

    2. Per-tenant analytics queries (`Message.objects.filter(role=...)`
       under `tenant_scope`) become a single index scan instead of a
       join through Conversation.

    The invariant `Message.tenant_id == Message.conversation.tenant_id`
    is enforced at write-time in B3's `record_message()` — there is no
    DB-level constraint because Django doesn't express cross-FK
    equality without a trigger. A drift would require a manual write
    path that bypasses `record_message()`; B3's tests pin the contract.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        TOOL = "tool", "Tool"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="messages",
        help_text="Denormalised from conversation.tenant. B3 service "
        "enforces the mirror invariant on every record_message() call.",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(
        blank=True,
        default="",
        help_text="Source-of-truth message body from the LLM / handler. "
        "Markdown / structured. Rendering for the channel happens "
        "downstream and is captured in `rendered_text`.",
    )
    rendered_text = models.TextField(
        blank=True,
        default="",
        help_text="Channel-rendered text — what the user actually saw. "
        "Captured for support / forensic replay.",
    )
    action_type = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Action namespace attached to this assistant message: "
        "show_masters / show_slots / confirm_booking / etc. Sprint 3+.",
    )
    action_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Structured payload backing the UI action (see action_type). Never raw PII.",
    )
    tool_call = models.JSONField(
        null=True,
        blank=True,
        help_text="Raw OpenAI tool_call object for forensic audit.",
    )
    tool_call_id = models.CharField(max_length=64, blank=True, default="")
    trace_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="From current_trace_id() via record_message(). Links "
        "this row to the WebhookJournal / Redis Stream / AuditLog / "
        "Event chain for the same pipeline turn.",
    )
    tokens_in = models.IntegerField(default=0)
    tokens_out = models.IntegerField(default=0)
    latency_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Message"
        verbose_name_plural = "Messages"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["role", "action_type"]),
        ]

    def __str__(self) -> str:
        preview = (self.content or self.rendered_text)[:40]
        return f"Message[{self.role}]({preview!r})"


class AiDraft(models.Model):
    """LLM-generated reply suggestion for a master to act on (M6 / Bundle B item 4).

    A *transient* artifact: replaced when regenerated, marked terminal
    when the master sends as themselves / releases to AI / dismisses.
    Drafts are NOT stored in :class:`Message` — they aren't messages
    until sent. Once sent, a new :class:`Message` row with
    ``action_type="master_compose"`` (or plain ``"assistant"``) is
    created and the draft moves to a terminal status.

    ### Status lifecycle

    ``ACTIVE`` is the only writable state. Every other state is terminal::

        ACTIVE ─┬─→ SENT_AS_MASTER     (master tapped «Отправить от себя»)
                ├─→ RELEASED_TO_AI     (master tapped «Пусть помощник ответит»)
                ├─→ REPLACED           (master regenerated)
                └─→ DISMISSED          (reserved — endpoint not in this PR)

    Per-conversation invariant: at most one ``ACTIVE`` row exists at any
    time. Enforced via the partial unique constraint
    :attr:`ai_draft_one_active_per_conversation` and a
    ``select_for_update`` lock in the service layer (which also marks
    the previous ACTIVE row as REPLACED in the same transaction).

    ### Tenant scoping

    Default manager filters by ``current_tenant()``. Service code that
    needs cross-tenant access (cleanup sweeps, replay) goes through
    :attr:`all_tenants`. The master detail GET reads via the tenant-
    scoped manager so a cross-tenant master can't see a foreign draft
    even if a UUID collision happened.

    ### Spec quote (master-mobile §M6 line 706-712)

        «When master taps «Отправить от себя» on a draft, the message
        renders to the customer as «Помощник: …». Same single assistant
        identity. Master's authorship is recorded in attribution
        metadata (``actor_type=master``, ``composed_by=master_id``)»

    ### Spec quote (master-mobile §M6 lines 662-671 — the draft card)

        «✨ Предложенный ответ ... [Отправить от себя] [Отредактировать]
        [Пусть помощник ответит]»

    The three action endpoints in :mod:`apps.master_api.services.ai_drafts`
    map 1:1 to these three buttons.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SENT_AS_MASTER = "sent_as_master", "Sent by master"
        RELEASED_TO_AI = "released_to_ai", "Released to AI auto-send"
        REPLACED = "replaced", "Replaced by newer draft"
        DISMISSED = "dismissed", "Dismissed by master"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="ai_drafts",
        help_text="Owning tenant. CASCADE — drafts are transient artifacts; "
        "tenant deletion already cascades through booking + conversation.",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="ai_drafts",
        help_text="Conversation this draft suggests a reply for.",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="+",
        help_text="Master the draft is suggested to.",
    )
    content = models.TextField(
        help_text="The LLM-generated reply text — 1-3 sentences typical.",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    trigger_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The most recent customer message at generation time. "
        "Used for the 60s idempotency window: if the master taps "
        "«Generate» twice within 60 seconds AND no new customer message "
        "arrived in between, we return the existing draft instead of "
        "billing another LLM call.",
    )
    llm_provider = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Provider name as reported by the LLM router ('openai' / 'anthropic').",
    )
    llm_model = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Model id echoed by the provider (e.g. 'gpt-4o-mini').",
    )
    llm_cost_usd = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=0,
        help_text="USD cost of the single LLM call. Computed via "
        "apps.llm.pricing.compute_cost. 0 on stub / unknown model.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "AI draft"
        verbose_name_plural = "AI drafts"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(status="active"),
                name="ai_draft_one_active_per_conversation",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "master", "status"]),
            models.Index(fields=["conversation", "-created_at"]),
        ]

    def __str__(self) -> str:
        preview = (self.content or "")[:40]
        return f"AiDraft[{self.status}]({preview!r})"
