"""Prompt registry read API (DRF-481 / Sprint 4 / A6).

Three cached lookup functions the orchestrator hits on every turn:

  * :func:`get_prompt` — active PromptVersion for (tenant, skill_name)
  * :func:`get_threshold` — Decimal for (tenant, key, applied_to)
                            with fallback to global ("") applied_to
  * :func:`get_disclaimer` — disclaimer text for (tenant, category,
                             risk_level)

All three are O(1) on cache hit; O(1 DB SELECT) on miss, then cached
for the rest of the process lifetime (or until pub/sub invalidates).

### Tenant resolution

Each function takes a ``Tenant`` explicitly rather than reading
``current_tenant()`` — call-site clarity. Orchestrator passes
``current_tenant()`` once at the top of the pipeline and threads it
through.

### Threshold fallback chain

``get_threshold(tenant, key, applied_to="faq")`` resolves:
  1. Active row for (tenant, key, applied_to="faq") — skill-specific
  2. Active row for (tenant, key, applied_to="") — global default
  3. ``KeyError`` — neither exists, caller must handle
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from apps.promptreg import cache as cache_mod
from apps.promptreg.models import DisclaimerLibrary, PromptVersion, ThresholdConfig

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


def get_prompt(tenant: "Tenant", skill_name: str) -> PromptVersion:
    """Return the active PromptVersion for (tenant, skill_name).

    Raises:
      PromptVersion.DoesNotExist: no active version published.
    """

    cached = cache_mod.get_prompt_cached(tenant.id, skill_name)
    if cached is not None:
        return cached

    pv = (
        PromptVersion.all_tenants.filter(tenant_id=tenant.id, skill_name=skill_name, is_active=True)
        .order_by("-published_at", "-version")
        .first()
    )
    if pv is None:
        raise PromptVersion.DoesNotExist(
            f"No active PromptVersion for tenant={tenant.id} skill={skill_name}"
        )
    cache_mod.put_prompt(tenant.id, skill_name, pv)
    return pv


def get_threshold(tenant: "Tenant", key: str, *, applied_to: str = "") -> Decimal:
    """Return the active threshold value for (tenant, key, applied_to).

    Fallback chain:
      1. Exact match on (tenant, key, applied_to)
      2. Global default (tenant, key, applied_to="")
      3. Raises KeyError

    Args:
      tenant: tenant to look up under.
      key: threshold identifier (e.g. "intent_confidence_min").
      applied_to: skill name, or "" for global.
    """

    cached = cache_mod.get_threshold_cached(tenant.id, key, applied_to)
    if cached is not None:
        return Decimal(cached.value)

    tc = (
        ThresholdConfig.all_tenants.filter(
            tenant_id=tenant.id, key=key, applied_to=applied_to, is_active=True
        )
        .order_by("-version")
        .first()
    )
    if tc is None and applied_to != "":
        # Try global fallback.
        return get_threshold(tenant, key, applied_to="")
    if tc is None:
        raise KeyError(
            f"No active ThresholdConfig for tenant={tenant.id} key={key} applied_to={applied_to!r}"
        )
    cache_mod.put_threshold(tenant.id, key, applied_to, tc)
    return Decimal(tc.value)


def get_disclaimer(tenant: "Tenant", category: str, risk_level: str) -> str:
    """Return the active disclaimer text for (tenant, category, risk_level).

    Raises:
      DisclaimerLibrary.DoesNotExist: no active disclaimer.
    """

    cached = cache_mod.get_disclaimer_cached(tenant.id, category, risk_level)
    if cached is not None:
        return cached.text

    dl = (
        DisclaimerLibrary.all_tenants.filter(
            tenant_id=tenant.id,
            category=category,
            risk_level=risk_level,
            is_active=True,
            withdrawn_at__isnull=True,
        )
        .order_by("-version")
        .first()
    )
    if dl is None:
        raise DisclaimerLibrary.DoesNotExist(
            f"No active disclaimer for tenant={tenant.id} "
            f"category={category} risk_level={risk_level}"
        )
    cache_mod.put_disclaimer(tenant.id, category, risk_level, dl)
    return dl.text
