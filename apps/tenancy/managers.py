"""TenantScopedManager — ORM-level tenant scoping (DRF-420 / A4).

Every domain model that holds tenant-owned rows declares:

    class MyModel(models.Model):
        tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.PROTECT)
        ...
        objects = TenantScopedManager()
        all_tenants = models.Manager()  # escape hatch for admin/maintenance

The default ``objects`` manager filters queries by ``current_tenant()``
on every read. The ``all_tenants`` manager skips the filter — explicit
opt-in for code that genuinely needs to see all tenants (cleanup tasks,
billing reconciliation, the cross-tenant leakage scanner itself).

The behaviour of ``objects`` depends on ``settings.STRICT_TENANT_SCOPE``:

  ┌─────────┬────────────────────────────────────────────────────────────┐
  │ mode    │ behaviour                                                  │
  ├─────────┼────────────────────────────────────────────────────────────┤
  │ strict  │ current_tenant() is None  → raise CrossTenantError         │
  │         │ current_tenant() set      → filter tenant=current          │
  │         │ filter(tenant_id=other)   → raise CrossTenantError         │
  ├─────────┼────────────────────────────────────────────────────────────┤
  │ audit   │ current_tenant() is None  → return empty + write_audit     │
  │         │ current_tenant() set      → filter tenant=current          │
  │         │ filter(tenant_id=other)   → return empty + write_audit     │
  ├─────────┼────────────────────────────────────────────────────────────┤
  │ off     │ no filter applied — returns the unfiltered queryset.       │
  │         │ Reserved for environments with multi-tenancy disabled.     │
  └─────────┴────────────────────────────────────────────────────────────┘

Why ORM-level instead of view-level (Ayla's IsTenantMember pattern):
- View-level only protects HTTP endpoints. Workers, Celery tasks, replay
  consumers, admin shells all bypass it. Putting the filter at the
  ORM level catches every code path uniformly.
- The escape hatch (``all_tenants``) is explicit and grep-able. A code
  review can search for ``.all_tenants.`` in apps/** and audit each
  legitimate usage.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models

from apps.tenancy.context import current_tenant
from apps.tenancy.exceptions import CrossTenantError

if TYPE_CHECKING:
    from django.db.models.query import QuerySet


logger = logging.getLogger(__name__)


VALID_SCOPE_MODES = ("strict", "audit", "off")


def _mode() -> str:
    mode = str(getattr(settings, "STRICT_TENANT_SCOPE", "audit")).lower()
    if mode not in VALID_SCOPE_MODES:
        logger.warning(
            "tenancy.manager.invalid_scope_mode mode=%r — defaulting to audit",
            mode,
        )
        return "audit"
    return mode


def _audit_log(action: str, **extra: Any) -> None:
    """Best-effort audit log for tenant-scope violations.

    Sprint 8 review P1-cycle1: wired via the
    :mod:`apps.tenancy.audit_hook` callback registry — ``apps.audit``
    registers its ``write_audit`` at AppConfig ``ready()`` time. This
    inverts the previous lazy-import dependency (foundation→domain).

    Keeps a fallback structured log line so violations are visible
    even when the audit writer is unregistered (tests, early boot).
    """
    from apps.tenancy.audit_hook import write as audit_write

    audit_write(f"tenancy.scope.{action}", payload=dict(extra))
    logger.warning("tenancy.scope.%s extra=%r", action, extra)


class TenantScopedManager(models.Manager):
    """Default manager: scopes every read to ``current_tenant()``.

    See module docstring for behaviour under audit / strict / off.
    """

    def get_queryset(self) -> "QuerySet[Any]":
        mode = _mode()
        base = super().get_queryset()

        if mode == "off":
            return base

        tenant = current_tenant()

        if tenant is None:
            if mode == "strict":
                raise CrossTenantError(
                    f"{self.model.__name__}.objects accessed without "
                    "a tenant context. Either enter `tenant_scope(t)` or "
                    "use `.all_tenants` for legitimate cross-tenant access.",
                )
            # audit: silently return empty, log the miss
            _audit_log(
                "queryset_without_context",
                model=self.model.__name__,
                mode=mode,
            )
            return base.none()

        return base.filter(tenant=tenant)

    def filter(self, *args: Any, **kwargs: Any) -> "QuerySet[Any]":
        """Filter override that catches explicit cross-tenant attempts.

        The ``get_queryset()`` filter already restricts to the current
        tenant. If a caller *also* passes ``tenant_id=...`` (or
        ``tenant=...``) with a value that disagrees with the current
        tenant, the intersection is empty *for the right reason*, but we
        want to flag the attempt instead of silently returning empty.
        """

        mode = _mode()
        tenant = current_tenant()

        if mode != "off" and tenant is not None:
            tenant_kwarg = kwargs.get("tenant")
            requested = kwargs.get("tenant_id") or (
                getattr(tenant_kwarg, "pk", None)
                if isinstance(tenant_kwarg, models.Model)
                else None
            )
            # Normalise to string before equality. Django accepts both
            # ``tenant_id=uuid_instance`` and ``tenant_id=str(uuid)`` in
            # ORM filters, so we must too. Without normalisation,
            # ``str(uuid)`` != ``UUID(uuid)`` and legitimate callers
            # trip a false cross-tenant detection.
            if requested is not None and str(requested) != str(tenant.id):
                if mode == "strict":
                    raise CrossTenantError(
                        f"{self.model.__name__}.objects.filter(tenant_id={requested}) "
                        f"attempted while current_tenant()={tenant.id}. "
                        "Use `.all_tenants.filter(...)` for legitimate "
                        "cross-tenant access.",
                    )
                _audit_log(
                    "explicit_cross_tenant_filter",
                    model=self.model.__name__,
                    current=str(tenant.id),
                    requested=str(requested),
                    mode=mode,
                )
                # audit: short-circuit to empty (the intersection is
                # empty anyway, but we want the audit-log breadcrumb)
                return super().get_queryset().none()

        return super().filter(*args, **kwargs)
