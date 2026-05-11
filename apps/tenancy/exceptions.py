"""Tenancy-specific exceptions (DRF-420 / A4)."""

from __future__ import annotations


class CrossTenantError(Exception):
    """Raised when code attempts to access another tenant's data.

    Two cases trigger this:
      1. A read happens with ``current_tenant() is None`` while
         ``STRICT_TENANT_SCOPE="strict"`` — the read has no tenant context
         and we refuse to serve "all rows of the table" as a default.
      2. A filter argument names a ``tenant_id`` that does not match the
         current ``current_tenant().id`` — the caller is explicitly trying
         to read another tenant's rows.

    In ``audit`` mode the manager logs to ``apps.audit`` and returns an
    empty queryset instead of raising — for the rollout window where we
    want to find offenders without breaking the app. In ``strict`` mode
    we want the request to fail loudly so the bug is fixed.
    """
