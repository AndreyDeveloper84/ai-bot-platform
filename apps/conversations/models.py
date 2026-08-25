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
    # DRF-1369 — the anonymisation cutoff (OD_MEMORY.md §4).
    #
    # NULL means «no part of this thread has been anonymised». A value means
    # every ``Message`` on this conversation with ``created_at <=
    # anonymized_through`` has had its body moved to :class:`ArchivedMessage`
    # and blanked in place.
    #
    # A CUTOFF and not a boolean, because «забудь всё» is not the end of the
    # dialogue: the person keeps talking to the bot afterwards, and the turns
    # they take after the request are theirs again. The account delete uses the
    # same field with the cutoff at the moment of deletion.
    #
    # Moving the cutoff forward is the only legal transition — see
    # ``apps.conversations.erasure.anonymize_dialogue``.
    anonymized_through = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="DRF-1369 / OD_MEMORY.md §4. Messages created at or before "
        "this instant are anonymised: body moved to ArchivedMessage, "
        "content/rendered_text blanked in place. NULL = nothing on this "
        "thread has been anonymised.",
    )
    anonymized_reason = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Which erasure request moved the cutoff — forget_all / "
        "account_delete. An audit cannot tell the two apart otherwise.",
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


class ArchivedMessage(models.Model):
    """The anonymised body of a :class:`Message`, kept for incident review.

    DRF-1369 / owner ruling ``OD_MEMORY.md`` §4, verbatim:

        «удалить всё» = удалить память/профиль и **обезличить переписку с
        гарантией недоступности для prompt pipeline»

    Three obligations. The third is the one that decides the shape of this
    table: a **guarantee**, not an intention.

    ### Why a separate table and not a flag on ``Message``

    The guarantee cannot be «every reader remembers to filter». The contour
    had already run that experiment: ``short_term.clear`` documented itself as
    «used by the 152-ФЗ delete-my-data workflow» and had no caller anywhere in
    ``apps/`` — the intention was written down, the guarantee was absent.

    So anonymisation does not mark the text; it **moves** it. ``Message.content``
    and ``Message.rendered_text`` are blanked in place, and the redacted body
    lands here. Every reader of the dialogue — the ones this ticket found, the
    ones it did not, and the ones written next month in a package nobody
    thought to grep — reads ``Message``. They get an empty string, by
    construction, with no filter to forget. Same posture the cascade already
    takes with ``StaffAssistantMessage.content`` (``privacy._erase_staff_
    assistant``) and with terminal ``AiDraft.content``; this is that pattern
    applied to the surface those two left uncovered.

    A column on ``Message`` would have been cheaper and weaker: a
    ``fields = "__all__"`` serialiser, a ``.values()``, ``model_to_dict``, or
    the Django admin would carry it back out again without anyone writing a
    read.

    ### What «обезличить» means here, precisely

    * The **words stay**. The owner's reason for keeping them is explicit —
      «это единственная запись того, что бот на самом деле сказал человеку, и
      она нужна при разборе инцидента и спора о брони». Deleting them would be
      erasure under another name, which the ruling rejects.
    * The **direct identifiers do not**. Bodies are passed through
      ``apps.replay.redactor.Redactor`` (``regex_v1``: phone, e-mail, card,
      OTP, tokened URL) before they are written here.
    * The **person key is deliberately kept** — ``conversation`` still points
      at the thread and the thread still points at the ``BotUser`` shell. A
      dispute about a booking is unresolvable without knowing whose booking it
      was, so this is pseudonymisation of the content, not severance of the
      row. Naming it rather than implying more than is done.

    ### Retention

    ``retention_until`` is stamped per row from
    ``ANONYMIZED_DIALOGUE_RETENTION_DAYS`` at archive time and enforced by
    ``apps.conversations.tasks.purge_expired_archived_messages``. The ruling
    demands the term be named — «бессрочно» is the absence of a decision — and
    a term nothing enforces is one more docstring promise, which is the exact
    failure DRF-1370 had to repair. See the module docstring of
    ``apps.conversations.erasure`` for how the 90 days was derived and for the
    open question standing with the owner.
    """

    class Reason(models.TextChoices):
        FORGET_ALL = "forget_all", "«Забудь всё» (memory erasure)"
        ACCOUNT_DELETE = "account_delete", "152-ФЗ delete-my-data"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="archived_messages",
        help_text="Mirrors the archived Message.tenant so the cross-tenant "
        "leakage scanner covers this model like every other.",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.PROTECT,
        related_name="archived_messages",
        help_text="PROTECT: the archive is the incident-review record; a "
        "stray conversation delete must not take it with it.",
    )
    message = models.OneToOneField(
        Message,
        on_delete=models.PROTECT,
        related_name="archived_body",
        help_text="The row whose body was moved here. OneToOne makes "
        "re-running the anonymiser a no-op at the database level rather "
        "than by the caller's good behaviour.",
    )
    role = models.CharField(max_length=16, help_text="Copied from Message.role.")
    body = models.TextField(
        blank=True,
        default="",
        help_text="Redacted Message.content. NEVER read by a prompt path — "
        "the sole sanctioned reader is "
        "apps.conversations.erasure.read_anonymized_dialogue.",
    )
    rendered_body = models.TextField(
        blank=True,
        default="",
        help_text="Redacted Message.rendered_text — what the person actually "
        "saw on their screen. Same read rule as `body`.",
    )
    action_type = models.CharField(max_length=32, blank=True, default="")
    action_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Redacted Message.action_data. It is not metadata: the "
        "clarification block carries the question the person was asked and "
        "the options they were offered, verbatim, and the MAX handler reads "
        "it back to rebuild a pending multi-select. Blanking content while "
        "leaving this behind left the words on a prompt path — the registry "
        "guard caught exactly that.",
    )
    tool_call = models.JSONField(
        null=True,
        blank=True,
        help_text="Redacted Message.tool_call — the model's arguments quote "
        "the person's phrasing. Archived for the same forensic reason the "
        "column exists at all.",
    )
    original_created_at = models.DateTimeField(
        help_text="Message.created_at. The archive orders by this, not by "
        "archived_at — a review reads the dialogue, not the sweep."
    )
    archived_at = models.DateTimeField(auto_now_add=True)
    retention_until = models.DateTimeField(
        db_index=True,
        help_text="The named term. purge_expired_archived_messages "
        "hard-deletes the row past this instant.",
    )
    reason = models.CharField(
        max_length=32,
        choices=Reason.choices,
        help_text="Which erasure request produced this row.",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Archived message"
        verbose_name_plural = "Archived messages"
        ordering = ["original_created_at"]
        indexes = [
            models.Index(fields=["conversation", "original_created_at"]),
        ]

    def __str__(self) -> str:
        return f"ArchivedMessage[{self.role}]({self.original_created_at:%Y-%m-%d})"


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


class StaffAssistantThread(models.Model):
    """A salon employee's working dialogue with the assistant (DRF-1061 step 0).

    ### Why not ``Conversation``

    ``Conversation`` is the customer thread, and eighteen modules read it
    on that assumption — the master's inbox, the handoff queue, the payment
    consumer, the booking follow-up sweep. Putting staff dialogue in the
    same table would make each of those a place where a missing filter
    becomes a silent defect: an employee appearing in their own list of
    customers, a follow-up chasing a colleague about a booking they never
    made. Today a staff row would slip past the master's inbox by accident
    (it filters on ``Exists(BookingRequest)``), and an accident is not a
    boundary.

    The two also want different columns. Half of ``Conversation`` is
    payment state, SLA tier and who-read-it-last — meaningless for someone
    asking how their Thursday looks.

    ### Why the name

    ``MasterAdminThread`` (apps.internal_chat) already means master↔admin.
    This is employee↔assistant. Three threads live in this product and each
    one needs a name that says who is talking.

    ### One active thread per person per salon

    Same partial-unique shape as ``conversation_one_active_per_bot_user_tenant``
    — proven here already, and it lets a closed thread stay as history while
    a new one opens.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="staff_assistant_threads",
        help_text="Owning salon. PROTECT — dropping a tenant must not "
        "silently erase what its staff were told.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.PROTECT,
        related_name="staff_assistant_threads",
        help_text="The employee. PROTECT mirrors Conversation.bot_user — "
        "deletion goes through the 152-ФЗ service, not a cascade.",
    )
    role_at_open = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="Primary role when the thread opened (owner / admin / "
        "receptionist / master). Not a duplicate of the resolver: it is "
        "what explains WHY the assistant answered as it did. After access "
        "is revoked (DRF-1227) the resolver returns 'customer', and "
        "without this the history stops making sense.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="False once closed. The active-uniqueness constraint depends on this flag.",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Soft-delete stamp (152-ФЗ «удалите мои данные»). "
        "Excluded from the uniqueness constraint so a replacement thread "
        "can open afterwards.",
    )
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Bumped by record_staff_message via atomic UPDATE, never "
        "instance .save() — concurrent turns must not trample each other.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Staff assistant thread"
        verbose_name_plural = "Staff assistant threads"
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "is_active", "-last_message_at"]),
            models.Index(fields=["bot_user", "-last_message_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "bot_user"],
                condition=models.Q(is_active=True, deleted_at__isnull=True),
                name="staff_assistant_thread_one_active",
            ),
        ]

    def __str__(self) -> str:
        return f"StaffAssistantThread[{self.id}]({self.role_at_open or 'no-role'})"

    def mark_deleted(self) -> None:
        """Soft-delete — same contract as ``Conversation.mark_deleted``."""

        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])


class StaffAssistantMessage(models.Model):
    """One turn inside a :class:`StaffAssistantThread`.

    Stored in Postgres rather than the Redis short-term window the customer
    path uses. That window expires in 24 hours, which suits a customer
    conversation and does not suit a working one: an employee's dialogue is
    interrupted — between clients, across shifts — and «I asked to take
    Tuesday off yesterday» has to still be there.

    ``tenant`` is denormalised off the thread, exactly as ``Message`` does
    it, so tenant-scoped reads never need the join.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        TOOL = "tool", "Tool"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        StaffAssistantThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="staff_assistant_messages",
        help_text="Denormalised from thread.tenant; the service enforces "
        "the mirror invariant on every write.",
    )
    seq = models.PositiveIntegerField(
        help_text="Position within the thread, 0-based. Ordering on "
        "``created_at`` alone is not enough: a tool round trip writes "
        "user / tool / assistant within a few milliseconds, and on a clock "
        "with coarse resolution those three share a timestamp — the history "
        "then comes back shuffled, handing the model an answer before its "
        "question. Assigned under a row lock on the thread.",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(
        blank=True,
        default="",
        help_text="What was said. For role=tool, the serialised result the assistant was handed.",
    )
    tool_name = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Which tool produced this turn. Empty for plain talk — "
        "the field is what makes «why did it answer that» answerable later.",
    )
    trace_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Links the turn to its WebhookJournal row.",
    )
    tokens_in = models.IntegerField(default=0)
    tokens_out = models.IntegerField(default=0)
    latency_ms = models.IntegerField(null=True, blank=True)
    llm_provider = models.CharField(max_length=32, blank=True, default="")
    llm_model = models.CharField(max_length=64, blank=True, default="")
    llm_cost_usd = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=0,
        help_text="USD cost of the call that produced this turn. Present "
        "from the start so the assistant's bill is answerable per person "
        "on day one, not after a second migration.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Staff assistant message"
        verbose_name_plural = "Staff assistant messages"
        ordering = ["created_at", "seq"]
        indexes = [
            models.Index(fields=["thread", "seq"]),
            models.Index(fields=["tenant", "-created_at"]),
        ]
        constraints = [
            # Belt to the row lock's braces: if two writers ever slip past
            # it, the loser fails here instead of silently duplicating a
            # position and reshuffling the history.
            models.UniqueConstraint(
                fields=["thread", "seq"],
                name="staff_assistant_message_seq_unique",
            ),
        ]

    def __str__(self) -> str:
        preview = (self.content or "")[:40]
        return f"StaffAssistantMessage[{self.seq}/{self.role}]({preview!r})"
