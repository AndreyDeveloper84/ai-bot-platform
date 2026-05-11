"""AuditLog model (DRF-426 / B1).

Audit-trail records — "who did what to whom under which tenant".
Forensic data: read-only in admin, retained per ``AUDIT_LOG_RETENTION_DAYS``
(default 90 days per review revision 6A-split).

Shape stays minimal in Sprint 1 — extra context goes into ``payload``
JSONB. Indices favour the two dominant query patterns: per-tenant audit
review (``(tenant, -created_at)``) and per-action investigation
(``(action, -created_at)``).
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


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

    # Default manager filters by current_tenant(). Use ``all_tenants`` for
    # admin / forensic / cleanup-task code that must see all rows.
    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Audit log"
        verbose_name_plural = "Audit logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self) -> str:
        suffix = f" target={self.target}:{self.target_id}" if self.target else ""
        return f"AuditLog[{self.action}{suffix}]"
