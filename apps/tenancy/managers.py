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
import re
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.tenancy.context import current_tenant
from apps.tenancy.exceptions import CrossTenantError

if TYPE_CHECKING:
    from django.db.models.query import QuerySet


logger = logging.getLogger(__name__)


VALID_SCOPE_MODES = ("strict", "audit", "off")

# Tenancy retro B1: lookup aliases that reach the ``tenant`` FK on a
# ``TenantScopedModel``. Without these the cross-tenant detector in
# ``TenantScopedManager.filter()`` inspects only ``tenant`` / ``tenant_id``
# bare kwargs and is silently bypassed by Django's standard lookup syntax
# (``tenant__id``, ``tenant_id__in``, ``tenant_id__exact``, ``tenant__pk``,
# etc.). The intersection with the ``get_queryset()`` scope filter is
# empty, but the audit row never gets written — bad telemetry on a real
# attempt + a latent vector if a future refactor changes the join shape.
#
# Matches any kwarg whose key starts with ``tenant`` and either equals it
# exactly OR continues with a Django lookup suffix (``__``). Examples
# caught: ``tenant``, ``tenant_id``, ``tenant__id``, ``tenant_id__in``,
# ``tenant__pk__exact``, ``tenant_id__isnull``. Not caught (correctly):
# unrelated field names that just happen to contain "tenant" as a
# substring later in the name (e.g. ``other_tenant_label``).
_TENANT_LOOKUP_RE = re.compile(r"^tenant(?:_id)?(?:__|$)")


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

        Tenancy retro B1: the cross-tenant detector walks both ``kwargs``
        (for Django lookup aliases like ``tenant__id``, ``tenant_id__in``)
        AND positional ``Q`` nodes in ``args``. Previously inspecting only
        ``kwargs.get("tenant")`` / ``kwargs.get("tenant_id")`` left a
        silent-bypass hole — ``filter(Q(tenant_id=other))`` AND'd with
        the scope filter yielded empty without an audit row.
        """

        mode = _mode()
        tenant = current_tenant()

        if mode != "off" and tenant is not None:
            mismatched = _collect_mismatched_tenant_values(args, kwargs, tenant.id)
            if mismatched:
                # Stringify the first offender for the error message;
                # log all mismatches in the audit payload for triage.
                first = mismatched[0]
                if mode == "strict":
                    raise CrossTenantError(
                        f"{self.model.__name__}.objects.filter(tenant_id={first}) "
                        f"attempted while current_tenant()={tenant.id}. "
                        "Use `.all_tenants.filter(...)` for legitimate "
                        "cross-tenant access.",
                    )
                _audit_log(
                    "explicit_cross_tenant_filter",
                    model=self.model.__name__,
                    current=str(tenant.id),
                    requested=[str(m) for m in mismatched],
                    mode=mode,
                )
                # audit: short-circuit to empty (the intersection is
                # empty anyway, but we want the audit-log breadcrumb).
                # Use ``self.none()`` so the empty queryset still carries
                # the scope filter — a future refactor that swaps
                # ``.none()`` for a returning queryset doesn't downgrade
                # to unscoped.
                return self.none()

        return super().filter(*args, **kwargs)


def _collect_mismatched_tenant_values(args: tuple, kwargs: dict, current_id: Any) -> list[Any]:
    """Return all ``tenant`` filter values that disagree with ``current_id``.

    Walks ``kwargs`` for any key matching :data:`_TENANT_LOOKUP_RE` AND
    positional ``Q`` nodes (recursing into nested ``Q`` children) for
    tuple-children whose key matches the same regex.

    Tenancy retro B1.1: ``__in`` lookups carry a LIST/tuple/set of ids,
    not a single value. The previous implementation compared the whole
    list-as-string against ``current_id``, which (a) false-positived on
    legitimate ``filter(tenant_id__in=[own.id])`` and (b) degraded
    audit telemetry on real mixed lists. Walk container values element
    by element so each id is compared individually.

    Skips negated Q nodes (``~Q(tenant_id=X)`` means «not this tenant»,
    not «cross-tenant lookup»). A defensive exclusion shouldn't trip
    the cross-tenant detector.
    """

    current_str = str(current_id)
    mismatches: list[Any] = []
    for key, value in kwargs.items():
        if _TENANT_LOOKUP_RE.match(key):
            mismatches.extend(_mismatches_from_value(key, value, current_str))
    for arg in args:
        if isinstance(arg, Q):
            mismatches.extend(_walk_q_for_mismatches(arg, current_str))
    return mismatches


def _mismatches_from_value(key: str, value: Any, current_str: str) -> list[Any]:
    """Return ``[value]`` if it disagrees with ``current_str``, else ``[]``.

    ``__in`` / ``__range`` lookups carry an iterable of ids; walk each
    element. Plain lookups carry a single id; treat as a one-element list
    of itself. ``__isnull`` lookups carry a bool — compare its
    stringified form against ``current_str`` (always disagrees → trip;
    that's the intended behavior, no one should call ``tenant__isnull``
    on a scoped manager).
    """

    # ``__in`` / ``__range`` → iterate elements.
    if key.endswith("__in") or key.endswith("__range"):
        if not isinstance(value, (list, tuple, set, frozenset)):
            # Defensive — Django would normally raise on a non-iterable
            # here, but better to surface the suspicious shape via the
            # audit row than crash inside the manager.
            return [value]
        out: list[Any] = []
        for elem in value:
            elem_id = getattr(elem, "pk", elem)
            if str(elem_id) != current_str:
                out.append(elem_id)
        return out
    # Plain lookup or unrelated suffix → single-value comparison.
    raw = getattr(value, "pk", value)
    if str(raw) != current_str:
        return [raw]
    return []


def _walk_q_for_mismatches(node: Q, current_str: str) -> list[Any]:
    """Recurse a ``Q`` tree returning every tenant-lookup mismatch.

    Negated nodes (``~Q(tenant_id=X)``) are skipped — they semantically
    mean «not this tenant», a defensive exclusion, not a cross-tenant
    lookup. The detector targets *positive* cross-tenant predicates.
    """

    if node.negated:
        return []

    out: list[Any] = []
    for child in node.children:
        if isinstance(child, Q):
            out.extend(_walk_q_for_mismatches(child, current_str))
            continue
        if isinstance(child, tuple) and len(child) == 2:
            key, value = child
            if isinstance(key, str) and _TENANT_LOOKUP_RE.match(key):
                out.extend(_mismatches_from_value(key, value, current_str))
    return out
