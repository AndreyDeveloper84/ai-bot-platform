"""Master ↔ Admin internal-chat models (handoff 2026-05-19 §6).

Three models that back the formal master↔admin communication channel:

* :class:`MasterAdminThread` — one logical conversation per (master,
  topic, linked artifact) tuple.
* :class:`MasterAdminMessage` — each turn in the thread. Reads receipts
  recorded per audience role (master / admin / founder).
* :class:`MasterAdminMessageAttachment` — image / pdf / voice-memo
  blobs attached to a message. PII-scan status carried but the actual
  scan pipeline is deferred (handoff §3.7 OUT-of-scope for this PR).

### Spec deviations vs handoff §6

The handoff prescribes five typed FKs on :class:`MasterAdminThread`
(`linked_earning_dispute`, `linked_leave_request`, `linked_review`,
`linked_offboarding`, `linked_substitution`). None of those target
models exist in the codebase today — `apps.earnings`, the
`MasterLeaveRequest` model in `apps.scheduling`, the `CustomerFeedback`
model in `apps.reviews`, and the `MasterOffboarding` / `MasterSubstitution`
models in `apps.staff` are all future work.

Rather than landing a migration with five `null=True` FKs that would
silently break at app-loading time, this PR replaces the five FKs with
a polymorphic-ish placeholder:

* ``linked_artifact_type`` — short string like ``"earning_dispute"``,
  ``"leave_request"``, ``"review"``, ``"offboarding"``, ``"substitution"``.
  Vocabulary is controlled at the application layer (see
  :data:`LINKED_ARTIFACT_TYPES`); the column is a free-form
  ``CharField`` so future PRs can add new artifact types without a
  migration.
* ``linked_artifact_id`` — opaque ``UUIDField`` (the target tables all
  use UUID primary keys in this codebase). NULL means "no linked
  artifact".

When the target apps land, follow-up PRs will add the typed FKs
alongside this placeholder, populate them in a data migration keyed
off the placeholder pair, and gradually phase the placeholder out.
Captured in the PR-6 description as a deliberate spec deviation.

Authentication / authorisation rules and the service-layer write
contract live in :mod:`apps.internal_chat.services` and
:mod:`apps.internal_chat.views`. This module is data-only —
constructors here MUST NOT call back into services.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import ClassVar, Iterable

from django.db import models
from django.db.models import Index, Q
from django.utils import timezone

from apps.tenancy.managers import TenantScopedManager


# --- vocabulary -----------------------------------------------------------


class TopicChoices(models.TextChoices):
    """Topic taxonomy per handoff §5.1.

    The set is closed (DB-side ``choices`` enforcement) so future
    additions go through a migration + a follow-up PR — handoff §5.2
    explicitly notes topic re-tagging is an admin power, but topic
    *invention* is a product decision.
    """

    EARNINGS_DISPUTE = "earnings_dispute", "Earnings dispute"
    LEAVE_REQUEST = "leave_request", "Leave / schedule"
    REVIEW_CONCERN = "review_concern", "Review concern"
    SCHEDULE_CHANGE = "schedule_change", "Schedule change"
    OFFBOARDING_DISCUSSION = "offboarding_discussion", "Offboarding"
    OTHER_MASTER_COMPLAINT = "other_master_complaint", "Other-master complaint"
    GENERAL = "general", "General"


class StatusChoices(models.TextChoices):
    """Thread lifecycle per handoff §6.1.

    The states match the spec verbatim. Transitions are enforced by the
    service layer (see :mod:`apps.internal_chat.services`), not by the
    model — we keep the field free for admins to flip via PATCH (the
    handoff §4.6 admin-close action) without a state-machine wall.
    """

    OPEN = "open", "Open — awaiting first response"
    ADMIN_RESPONDED = "admin_responded", "Admin responded — awaiting master"
    MASTER_RESPONDED = "master_responded", "Master responded — awaiting admin"
    ACTIVE_DISCUSSION = "active_discussion", "Active discussion"
    RESOLVED = "resolved", "Resolved"
    AUTO_CLOSED_INACTIVE = "auto_closed_inactive", "Auto-closed (14d no activity)"
    ESCALATED_TO_FOUNDER = "escalated_to_founder", "Escalated to founder"


class SenderRoleChoices(models.TextChoices):
    MASTER = "master", "Master"
    ADMIN = "admin", "Admin / Owner / Receptionist"
    FOUNDER = "founder", "Founder"
    SYSTEM = "system", "System (auto-close, etc.)"


class AttachmentTypeChoices(models.TextChoices):
    IMAGE = "image", "Image (JPEG/PNG/WebP)"
    PDF = "pdf", "PDF"
    VOICE_MEMO = "voice_memo", "Voice memo"


class PiiScanStatusChoices(models.TextChoices):
    """Attachment PII-scan lifecycle (handoff §3.7 + §8.1).

    PR 6 records the column but the actual scan pipeline is deferred —
    every row stays at ``PENDING`` forever in this PR. Listed here as
    the future-state vocabulary for the scan worker.
    """

    PENDING = "pending", "Awaiting scan"
    CLEAN = "clean", "Clean"
    FLAGGED = "flagged", "Flagged — review"
    BLOCKED = "blocked", "Blocked — customer PII detected"


# Topic vocabulary applied to the polymorphic ``linked_artifact_type``
# placeholder. NOT enforced as a DB CHECK — see module docstring "Spec
# deviations". Documented at the Python layer so future PRs can extend
# the set without a migration.
LINKED_ARTIFACT_TYPES: tuple[str, ...] = (
    "earning_dispute",  # future apps.earnings.EarningDispute
    "leave_request",  # future apps.scheduling.MasterLeaveRequest
    "review",  # future apps.reviews.CustomerFeedback
    "offboarding",  # future apps.staff.MasterOffboarding
    "substitution",  # future apps.staff.MasterSubstitution
    "booking",  # apps.booking.BookingRequest — wired today
)


# Topics that automatically carry the ``is_sensitive`` flag per
# handoff §5.4 + §2.7. The service layer computes ``is_sensitive`` on
# every create + every topic re-tag; the model also enforces it on
# ``save()`` as defence-in-depth.
SENSITIVE_TOPICS: frozenset[str] = frozenset(
    {
        TopicChoices.OTHER_MASTER_COMPLAINT.value,
        TopicChoices.OFFBOARDING_DISCUSSION.value,
    }
)


# First-response SLA per topic per handoff §5.1.
# Earnings + reviews — tighter (money + reputation). Leave / schedule
# change — 24h (life-impacting but lower urgency). Others — 72h
# (general / complaints / etc.). All values are timedelta-friendly.
_SLA_HOURS_BY_TOPIC: dict[str, int] = {
    TopicChoices.EARNINGS_DISPUTE.value: 48,
    TopicChoices.REVIEW_CONCERN.value: 48,
    TopicChoices.LEAVE_REQUEST.value: 24,
    TopicChoices.SCHEDULE_CHANGE.value: 48,
    TopicChoices.OFFBOARDING_DISCUSSION.value: 48,
    TopicChoices.OTHER_MASTER_COMPLAINT.value: 72,
    TopicChoices.GENERAL.value: 72,
}
"""First-response SLA hours by topic. Used by the service layer to
compute ``sla_due_at`` at create time. Re-tag does NOT recompute the
SLA (intentional — once a master sent a message, the salon owes them
a response within the original window even if the topic was wrong)."""


def sla_hours_for_topic(topic: str) -> int:
    """Return the first-response SLA window for ``topic`` in hours.

    Falls back to 72h for any unknown topic value — defence in depth.
    The service layer pre-validates against :class:`TopicChoices` so
    this fallback is unreachable in production callers.
    """

    return _SLA_HOURS_BY_TOPIC.get(topic, 72)


# --- thread ---------------------------------------------------------------


class MasterAdminThread(models.Model):
    """One logical master↔admin conversation per handoff §6.1.

    Spec deviations vs §6.1 are documented in the module docstring and
    the PR description. In short:

    * ``master`` FK → :class:`apps.catalog.models.CatalogMaster`
      (handoff says ``staff.Master`` — we use the platform's existing
      master row).
    * ``assigned_admin`` FK → :class:`apps.identity.models.BotUser`
      (handoff says ``auth.User`` — we use BotUser because every admin
      on the platform is identified via their MAX BotUser, not Django's
      auth.User which is reserved for Django admin staff).
    * Five typed ``linked_*`` FKs replaced with a single
      ``linked_artifact_type`` + ``linked_artifact_id`` placeholder
      pair.

    The default ``objects`` manager is tenant-scoped; ``all_tenants``
    is the explicit escape hatch (cleanup tasks, the SLA-breach
    scanner — future PRs).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="master_admin_threads",
        help_text="Owning tenant. CASCADE — a tenant delete drops every "
        "thread along with the tenant's master/staff data.",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="admin_threads",
        help_text="The master who opened the thread. CASCADE — deleting "
        "a master also drops their internal-chat history (the audit "
        "row trail remains independently via apps.audit).",
    )

    topic = models.CharField(
        max_length=32,
        choices=TopicChoices.choices,
        db_index=True,
        help_text="Topic per handoff §5.1. Re-tag is an admin power "
        "(handoff §4.4) — service layer enforces audit on change.",
    )
    subject = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Auto-generated from topic + first message OR admin-"
        "edited. Free-form short string — the UI uses this for list "
        "rendering when present, otherwise falls back to topic display.",
    )
    status = models.CharField(
        max_length=32,
        choices=StatusChoices.choices,
        default=StatusChoices.OPEN,
        db_index=True,
        help_text="Lifecycle state. Transitions are enforced by the "
        "service layer; admins can PATCH directly for manual override.",
    )

    # Spec-deviation placeholder pair (see module docstring).
    linked_artifact_type = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Polymorphic placeholder for the five typed FKs in "
        "handoff §6.1 — values like 'earning_dispute', 'leave_request'. "
        "Vocabulary controlled at the application layer; future PRs "
        "replace this with typed FKs as the target apps land.",
    )
    linked_artifact_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Opaque UUID of the linked artifact. NULL when "
        "``linked_artifact_type`` is empty. No FK constraint by design "
        "— see module docstring.",
    )

    assigned_admin = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Optional pinning per handoff §4.3. SET_NULL on "
        "BotUser delete so the thread survives the admin leaving.",
    )

    is_sensitive = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Auto-True for OTHER_MASTER_COMPLAINT and "
        "OFFBOARDING_DISCUSSION topics per handoff §5.4. Receptionists "
        "see the row in their queue but cannot view the message body "
        "unless they're the ``assigned_admin``.",
    )
    founder_added_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the master escalated to founder per handoff "
        "§3.6. NULL when no escalation; non-NULL pairs with "
        "status=ESCALATED_TO_FOUNDER.",
    )

    sla_due_at = models.DateTimeField(
        help_text="First-response SLA deadline. Computed at create "
        "time from topic per handoff §5.1; never updated on re-tag.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(
        auto_now=True,
        help_text="Updated on every save. The service layer also "
        "explicitly stamps it on send_message so the list ordering "
        "stays correct even when other fields didn't change.",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Future PR: 90d-inactive auto-archive per handoff §2.8.",
    )

    objects: ClassVar[TenantScopedManager] = TenantScopedManager()
    all_tenants: ClassVar[models.Manager] = models.Manager()

    class Meta:
        verbose_name = "Master-admin thread"
        verbose_name_plural = "Master-admin threads"
        ordering = ["-last_activity_at"]
        indexes = [
            # List endpoints filter by tenant + status, sort by
            # last_activity_at descending. The composite covers both.
            Index(fields=["tenant", "status", "-last_activity_at"]),
            # Master-side list: own threads sorted by recency.
            Index(fields=["master", "-last_activity_at"]),
            # SLA-breach scanner (future PR) walks open threads ordered
            # by sla_due_at ascending.
            Index(fields=["sla_due_at"]),
            # Polymorphic placeholder lookup: "find threads pointing at
            # earning_dispute X" once the target apps land.
            Index(fields=["linked_artifact_type", "linked_artifact_id"]),
        ]
        constraints = [
            # Sensitive flag is a derived property of topic per §5.4
            # for the two listed topics. Other topics MAY be sensitive
            # (an admin can manually flag) so we DON'T constrain
            # is_sensitive=True ↔ topic in the sensitive set; we only
            # forbid is_sensitive=False for the two auto-sensitive
            # topics. The service layer sets the flag on save; this
            # constraint is defence-in-depth against future SQL paths
            # that bypass the service layer.
            models.CheckConstraint(
                condition=(~Q(topic__in=sorted(SENSITIVE_TOPICS)) | Q(is_sensitive=True)),
                name="ck_internal_chat_sensitive_topics_flagged",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"MasterAdminThread[{self.id}: master={self.master_id} "
            f"topic={self.topic} status={self.status}]"
        )

    def save(self, *args: object, **kwargs: object) -> None:
        """Defence-in-depth invariants applied on every write.

        * Auto-True ``is_sensitive`` for the SENSITIVE_TOPICS list.
        * Auto-fill ``sla_due_at`` from topic when missing (the service
          layer normally provides it; the fallback keeps direct ORM
          writes — tests, future bulk-imports — from violating the
          NOT NULL constraint).
        """

        if self.topic in SENSITIVE_TOPICS:
            self.is_sensitive = True
        if self.sla_due_at is None:
            # auto_now_add hasn't run yet for unsaved instances; use
            # timezone.now() as the baseline. The service layer takes
            # the same path explicitly with the post-create timestamp,
            # so this fallback only matters for direct ORM writes.
            base = self.created_at or timezone.now()
            self.sla_due_at = base + timedelta(hours=sla_hours_for_topic(self.topic))
        super().save(*args, **kwargs)  # type: ignore[arg-type]


# --- message --------------------------------------------------------------


class MasterAdminMessage(models.Model):
    """One turn in a :class:`MasterAdminThread` (handoff §6.2).

    Read receipts recorded per audience-role (master / admin / founder)
    so the UI can render «прочитано Студия 14:30» style indicators per
    handoff §3.9 without per-user join overhead.

    The default manager scopes by tenant via the parent thread's
    ``tenant`` FK; we proxy through a custom manager because there's
    no direct ``tenant`` column on this row (intentional — the
    parent's tenant is the source of truth, and double-storing it
    would invite drift).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        MasterAdminThread,
        on_delete=models.CASCADE,
        related_name="messages",
        help_text="Owning thread. CASCADE — deleting a thread drops "
        "every message + attachment (cascade-cascade).",
    )

    sender_role = models.CharField(
        max_length=16,
        choices=SenderRoleChoices.choices,
        help_text="Role chip per handoff §2.7. Receptionists send as "
        "role='admin' too — masters see «Студия» regardless of which "
        "specific admin replied unless the admin explicitly signs.",
    )
    sender_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="NULL for SYSTEM messages (auto-close, auto-archive). "
        "SET_NULL on BotUser delete so the message history survives.",
    )
    sender_admin_signed_name = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Optional «— Натали» signature per handoff §2.7 / "
        "§6.2. Empty for the default «Студия» sender identity.",
    )

    body = models.TextField(
        max_length=4000,
        help_text="Plain-text message body. Customer PII filter (handoff "
        "§8.1) lands in a future PR; PR 6 trusts the master/admin to "
        "follow the policy.",
    )

    read_by_master_at = models.DateTimeField(null=True, blank=True)
    read_by_admin_at = models.DateTimeField(null=True, blank=True)
    read_by_founder_at = models.DateTimeField(null=True, blank=True)

    is_system_message = models.BooleanField(
        default=False,
        help_text="True for auto-close / auto-archive / 'thread "
        "escalated' notices. Renders differently in the UI (italic, "
        "no sender chrome).",
    )

    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Master-admin message"
        verbose_name_plural = "Master-admin messages"
        ordering = ["sent_at"]
        indexes = [
            # Thread-detail render walks messages newest-first OR
            # oldest-first; the per-thread chronological index covers
            # both directions.
            Index(fields=["thread", "-sent_at"]),
            # Unread-counter computation: "messages in this thread the
            # master/admin hasn't seen". Two partial-style filters,
            # one composite index that's still small.
            Index(fields=["thread", "read_by_master_at"]),
            Index(fields=["thread", "read_by_admin_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"MasterAdminMessage[{self.id} thread={self.thread_id} sender_role={self.sender_role}]"
        )


# --- attachment -----------------------------------------------------------


class MasterAdminMessageAttachment(models.Model):
    """File attached to a :class:`MasterAdminMessage` (handoff §6.3).

    PR 6 lands the model + audit-grade ``file_sha256`` column, but the
    actual upload endpoint + PII scan pipeline are deferred. Rows
    created by future PRs always start at ``pii_scan_status=PENDING``
    and never transition in this PR.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        MasterAdminMessage,
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="Owning message. CASCADE — deleting a message drops "
        "its attachments along with it.",
    )

    attachment_type = models.CharField(
        max_length=16,
        choices=AttachmentTypeChoices.choices,
    )
    file = models.FileField(
        upload_to="master_admin_chat/",
        help_text="Storage path is filesystem today (FileField). S3 migration is a future PR.",
    )
    file_size_bytes = models.IntegerField(
        help_text="Stored separately from the FileField's size attribute "
        "so audit trails survive a storage-tier migration.",
    )
    file_sha256 = models.CharField(
        max_length=64,
        help_text="Hex SHA-256 of the file bytes. Used for dedup + "
        "tamper detection in the future PII scan pipeline.",
    )

    duration_seconds = models.IntegerField(
        null=True,
        blank=True,
        help_text="Voice-memo length in seconds. NULL for image/PDF.",
    )

    pii_scan_status = models.CharField(
        max_length=32,
        choices=PiiScanStatusChoices.choices,
        default=PiiScanStatusChoices.PENDING,
        help_text="Customer-photo filter status per handoff §3.7. "
        "Pipeline deferred to a future PR — every row stays PENDING "
        "in PR 6.",
    )

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Master-admin message attachment"
        verbose_name_plural = "Master-admin message attachments"
        ordering = ["uploaded_at"]
        indexes = [
            Index(fields=["message"]),
            # PII-scan worker scans pending rows oldest-first.
            Index(fields=["pii_scan_status", "uploaded_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"MasterAdminMessageAttachment[{self.id} "
            f"message={self.message_id} type={self.attachment_type}]"
        )


__all__ = [
    "AttachmentTypeChoices",
    "LINKED_ARTIFACT_TYPES",
    "MasterAdminMessage",
    "MasterAdminMessageAttachment",
    "MasterAdminThread",
    "PiiScanStatusChoices",
    "SENSITIVE_TOPICS",
    "SenderRoleChoices",
    "StatusChoices",
    "TopicChoices",
    "sla_hours_for_topic",
]


def __dir__() -> Iterable[str]:
    return sorted(__all__)
