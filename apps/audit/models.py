"""AuditLog model (DRF-426 / B1; soft-delete extension DRF-851 / PI1).

Audit-trail records — "who did what to whom under which tenant".
Forensic data: read-only in admin, retained per ``AUDIT_LOG_RETENTION_DAYS``
(default 90 days per review revision 6A-split).

Shape stays minimal in Sprint 1 — extra context goes into ``payload``
JSONB. Indices favour the two dominant query patterns: per-tenant audit
review (``(tenant, -created_at)``) and per-action investigation
(``(action, -created_at)``).

### Soft-delete (DRF-851 / PI1)

When ``AUDIT_LOG_RETENTION_MODE="soft"`` the cleanup task flips
``is_archived=True`` + stamps ``archived_at`` instead of issuing a
DELETE. Archived rows are hidden from the default ``objects`` manager
but remain visible via :attr:`all_tenants` and :attr:`archived`
managers. A second (separate) future task can later hard-delete
archived rows past a longer cutoff — out of scope for this PR.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models

from apps.tenancy.context import current_tenant
from apps.tenancy.managers import TenantScopedManager


class _ActiveAuditLogManager(TenantScopedManager):
    """Default manager: tenant-scoped AND hides ``is_archived=True``.

    Soft-archived rows are retention residue; everyday read paths
    should not see them. Forensic / archive-only queries must opt
    in via :attr:`AuditLog.archived` or :attr:`AuditLog.all_tenants`.
    """

    def get_queryset(self) -> "models.QuerySet[Any]":
        return super().get_queryset().filter(is_archived=False)


class _ArchivedAuditLogManager(models.Manager):
    """Cross-tenant view of archived rows only (for forensic queries)."""

    def get_queryset(self) -> "models.QuerySet[Any]":
        qs = super().get_queryset().filter(is_archived=True)
        tenant = current_tenant()
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return qs


class AuditLog(models.Model):
    """A single audit-trail row.

    Fields:
      tenant       Nullable FK. Tenant-rotation events / system actions
                   have no current tenant; we still want the row.
      actor_id     Nullable UUID. System actions (Celery tasks, breakers)
                   have no human actor.
      action       Namespaced verb: ``tenant.created``,
                   ``cross_tenant.attempt``, ``breaker.opened``, etc.
      target       Optional human-readable model name (``Tenant``,
                   ``Conversation``).
      target_id    Optional UUID of the affected row.
      payload      JSONB for extra structured data. Never raw PII.
      created_at   Always auto-set.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
        help_text="Tenant in scope at the time of the event. Null for "
        "system events (tenant rotation, breaker state changes).",
    )
    actor_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="UUID of the human actor, if any. Null for system events.",
    )
    action = models.CharField(
        max_length=120,
        help_text="Namespaced verb, e.g. tenant.created, cross_tenant.attempt.",
    )
    target = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="Model name of the affected entity (Tenant, Conversation, ...).",
    )
    target_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Extra structured data. Never store raw PII; store IDs and hashes.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Soft-delete fields (DRF-851 / PI1). When the cleanup task runs in
    # ``AUDIT_LOG_RETENTION_MODE="soft"`` it flips ``is_archived=True``
    # + stamps ``archived_at`` instead of issuing a DELETE.
    is_archived = models.BooleanField(
        default=False,
        help_text="Soft-delete flag flipped by cleanup task in 'soft' "
        "retention mode. Default manager hides these rows; use the "
        "'archived' or 'all_tenants' manager to query them.",
    )
    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when ``is_archived`` was flipped to True. "
        "NULL for live rows. A future task may use this to hard-delete "
        "archived rows past a longer cutoff.",
    )

    # Default manager filters by current_tenant() AND hides archived rows.
    # Use ``all_tenants`` for admin / forensic / cleanup-task code that
    # must see every row regardless of tenant or archive state.
    # Use ``archived`` to query only soft-deleted rows.
    objects = _ActiveAuditLogManager()
    all_tenants = models.Manager()
    archived = _ArchivedAuditLogManager()

    class Meta:
        verbose_name = "Audit log"
        verbose_name_plural = "Audit logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
            # PI1: efficient archive-only sweeps for the future hard-delete task.
            models.Index(fields=["is_archived", "archived_at"]),
        ]

    def __str__(self) -> str:
        suffix = f" target={self.target}:{self.target_id}" if self.target else ""
        return f"AuditLog[{self.action}{suffix}]"
