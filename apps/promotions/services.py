"""Promo-code validation entry point (DRF-842 / Phase 1 / B6).

:func:`validate_promo` is the single source of truth for "is this
promo code usable RIGHT NOW for this service on this tenant?". The
``calc_price`` LLM tool (see :mod:`apps.skills.booking.tools`) is the
only caller today; a future checkout flow / payments path will reuse it.

### Reason taxonomy (7-value enum)

Returned by :class:`PromoValidation.reason`:

* ``ok``            — promo applies, ``discount_percent`` is honoured.
* ``not_found``     — no row matches ``(tenant, code)``.
* ``inactive``      — row exists but ``is_active=False`` (admin kill-switch).
* ``not_started``   — ``valid_from > now``.
* ``expired``       — ``valid_to < now``.
* ``wrong_service`` — non-empty ``applicable_service_ids`` does not
                      include the queried ``service_id``.

The tool turns each value into a specific user-facing Russian copy
template (see :func:`apps.skills.booking.tools._copy_for_promo_status`).
Keeping the taxonomy in the validator (not the tool) means a future
checkout flow gets identical reason fidelity for free.

### Never raises

All branches return a :class:`PromoValidation`. Database-level errors
propagate (the caller is wrapped in skill-level exception handling
already); validation-domain errors are values, not exceptions. Same
principle as :func:`apps.skills.booking.tools.show_masters` — domain
errors are part of the function's range.

### Case-insensitive matching

The query upper-cases the input ``code`` before the lookup. Storage
upper-cases at save time (see :meth:`Promotion.save`). The pair
guarantees the comparison is case-insensitive without a Postgres-
specific functional index.

### Tenant scoping

Caller passes ``tenant`` explicitly; the query uses
``Promotion.all_tenants.filter(tenant=tenant, ...)``. Reason: the
LLM tool runs under a tenant-scope ContextVar but the call site is
deeply nested under async/sync boundaries — passing the tenant
explicitly is more legible and matches the rest of the booking-
skill tool signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from django.utils import timezone as dj_timezone

from apps.promotions.models import Promotion
from apps.tenancy.models import Tenant

PromoReason = Literal[
    "ok",
    "not_found",
    "inactive",
    "not_started",
    "expired",
    "wrong_service",
]


@dataclass(frozen=True)
class PromoValidation:
    """Result of :func:`validate_promo`.

    Frozen — the validator is pure; callers should not mutate the
    result. ``promo`` is non-None only when ``ok=True``.
    """

    ok: bool
    promo: Promotion | None
    reason: PromoReason


def validate_promo(
    *,
    tenant: Tenant,
    code: str,
    service_id: UUID,
    now: datetime | None = None,
) -> PromoValidation:
    """Validate a promo code for a specific service on a tenant.

    Args:
      tenant: the owning :class:`apps.tenancy.models.Tenant`. Passed
              explicitly (not pulled from ContextVar) — keeps the
              call site at the tool layer legible.
      code: user-supplied promo code, free-case (the validator
            upper-cases before the query).
      service_id: the :class:`apps.catalog.models.CatalogService` UUID
                  pk the customer wants to apply the code to.
      now: override clock for tests. Defaults to ``django.utils.timezone.now()``.

    Returns:
      A :class:`PromoValidation`. ``ok=True`` means the caller may
      apply ``promo.discount_percent`` to the service base price.
      ``ok=False`` carries the precise ``reason`` so the caller can
      render a tailored response.
    """
    now = now or dj_timezone.now()
    normalised = (code or "").strip().upper()
    if not normalised:
        return PromoValidation(ok=False, promo=None, reason="not_found")

    # We use ``all_tenants`` + explicit ``tenant=`` so the caller's
    # current_tenant() context isn't required (e.g. background tasks
    # / inline tool dispatch don't always set it). Cross-tenant leak
    # impossibility comes from the explicit ``tenant=tenant`` predicate.
    promo = Promotion.all_tenants.filter(
        tenant=tenant,
        code=normalised,
    ).first()

    if promo is None:
        return PromoValidation(ok=False, promo=None, reason="not_found")

    if not promo.is_active:
        return PromoValidation(ok=False, promo=None, reason="inactive")

    if promo.valid_from is not None and promo.valid_from > now:
        return PromoValidation(ok=False, promo=None, reason="not_started")

    if promo.valid_to is not None and promo.valid_to < now:
        return PromoValidation(ok=False, promo=None, reason="expired")

    applicable = promo.applicable_service_ids or []
    if applicable and not _service_in_list(service_id, applicable):
        return PromoValidation(ok=False, promo=None, reason="wrong_service")

    return PromoValidation(ok=True, promo=promo, reason="ok")


def _service_in_list(service_id: UUID, applicable: list[object]) -> bool:
    """Membership check tolerant of UUID-vs-string storage.

    The JSON column stores UUIDs as strings (JSON has no UUID type);
    the caller passes a real :class:`uuid.UUID`. Normalise both sides
    to strings before comparing so the lookup works regardless of
    whether the row was hand-edited via shell (might have UUID
    instances) or admin (strings).
    """
    target = str(service_id)
    for entry in applicable:
        if str(entry) == target:
            return True
    return False
