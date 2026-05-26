"""Master Mini App services list (master-solo-surface §4.4).

Read-only list of services this master performs, sourced from the
existing :class:`apps.catalog.models.MasterService` mapping (PR #518).
Powers the "Услуги" tab in the solo provider Mini App per Tau's Variant B
navigation verdict.

### Scope (Tier 2 Phase 1)

* Active services the master is mapped to via ``MasterService``.
* Per-service fields: name, price, duration, description, category-ish
  grouping (best-effort via the service slug prefix), active flag.

### Out of scope (deferred post-pilot — see W1 tracking issues)

* Service edit / add / archive — canonical state lives in Ayla per
  ADR-0009 §Hard rule #4 (no new transactional domains in bot-platform).
  bot-platform may only READ the mirror.
* Per-region pricing reference data ("Регион: Пенза · средняя по городу
  2200 ₽" per Tau §4.4) — source TBD, likely an Alpha aggregation.
* Drag-reorder display ordering — Alpha-side mutation needed.

### Tenant safety

Same as :mod:`apps.master_api.services.customers` — explicit
``tenant_id`` filter on every query as defence-in-depth even though the
auth decorator already entered ``tenant_scope``.

### Category grouping note

Tau §4.4 groups services by category ("── Маникюр ──" / "── Педикюр ──"
sections). The :class:`apps.catalog.models.CatalogService` mirror does
NOT carry a first-class category field today — the legacy mysite model
joins through ``related_services`` / ``category_slug`` on a sibling
table that wasn't mirrored. Phase 1 workaround: derive a coarse
category label from the service slug's first dash-prefix (e.g.
``manicure-gel`` → ``manicure``). When no useful prefix exists (slug is
a single word) we fall back to a generic "Услуги" bucket. Full category
support is a post-pilot follow-up tied to the catalog edit tracking
issue.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from apps.catalog.models import CatalogMaster, CatalogService, MasterService

logger = logging.getLogger(__name__)


# Tunables ----------------------------------------------------------------

DEFAULT_CATEGORY = "Услуги"
"""Fallback bucket name when a service slug doesn't reveal a category.
Per Tau §4.4 "Single service: skip category grouping (group too small)"
the frontend may still hide grouping when only one bucket exists."""

# Coarse slug-prefix → human label mapping. Intentionally tiny — Phase 1
# scope is read-only and the salon catalog vocab is bounded for the pilot
# tenant. Unknown prefixes fall through to the title-cased prefix itself
# so even an un-mapped service gets a sensible group header (e.g.
# "spa-relax" → "Spa").
_CATEGORY_LABEL_OVERRIDES: dict[str, str] = {
    "manicure": "Маникюр",
    "pedicure": "Педикюр",
    "hair": "Стрижки",
    "haircut": "Стрижки",
    "color": "Окрашивание",
    "facial": "Уход за лицом",
    "massage": "Массаж",
    "wax": "Эпиляция",
    "epil": "Эпиляция",
    "brow": "Брови",
    "lash": "Ресницы",
    "spa": "SPA",
    "nail": "Ногти",
    "make": "Макияж",
    "makeup": "Макияж",
}


def _derive_category(service: CatalogService) -> str:
    """Best-effort category label from the service slug.

    Slug shape is ``"category-detail-modifier"`` per mysite convention
    (e.g. ``manicure-gel``, ``pedicure-apparatnyy``). We take the first
    dash-segment, lowercase it, then look it up in the override map.
    Falls back to the title-cased prefix itself, or to :data:`DEFAULT_CATEGORY`
    when the slug is empty.
    """

    slug = (service.slug or "").strip().lower()
    if not slug:
        return DEFAULT_CATEGORY
    head = slug.split("-", 1)[0]
    if not head:
        return DEFAULT_CATEGORY
    label = _CATEGORY_LABEL_OVERRIDES.get(head)
    if label is not None:
        return label
    # Capitalise the slug prefix as a last-resort label so the UI still
    # gets a non-empty group header.
    return head[:1].upper() + head[1:]


def _price_rub(raw: Decimal | None) -> int | None:
    """Convert :attr:`CatalogService.price_from` to an integer RUB value.

    ``price_from`` is a :class:`Decimal` — the Tau spec renders prices as
    whole roubles ("2400 ₽"), so we round to the nearest int. Returns
    ``None`` when the source value is NULL (legacy mirror rows / services
    priced "по запросу").
    """

    if raw is None:
        return None
    try:
        return int(raw.to_integral_value())
    except (ArithmeticError, ValueError, TypeError):
        return None


# Public API --------------------------------------------------------------


def list_master_services(*, master: CatalogMaster) -> list[dict[str, Any]]:
    """Return the active services list for ``master``.

    Sorted by ``(category ASC, name ASC)`` per Tau §4.4 grouping spec.
    Empty list when the master has no service mappings — caller renders
    the empty-state copy ("Здесь будет твой каталог услуг. Карина
    настроит вместе с тобой.").

    Each dict has the shape (all keys always present):

    .. code-block:: python

        {
            "service_id": "<uuid>",
            "name": "Маникюр гель-лак",
            "price_rub": 2400,             # None for legacy / on-request
            "duration_min": 90,            # 0 when unknown (rare)
            "description": "Базовый маникюр + покрытие гель-лак",
            "category": "Маникюр",
            "is_active": True,
        }

    ### Filtering rules

    * ``MasterService.master == master`` — strict mapping.
    * ``CatalogService.is_active == True`` — archived services hidden.
      Tau §4.4 reserves a separate "Архив" disclosure (deferred Tier 2).
    * Cross-tenant defence: explicit ``tenant_id`` filter on both
      :class:`MasterService` and :class:`CatalogService` queries.
    """

    mapping_rows = MasterService.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
    ).values_list("service_id", flat=True)
    service_ids = list(mapping_rows)
    if not service_ids:
        return []

    services = CatalogService.all_tenants.filter(
        tenant_id=master.tenant_id,
        id__in=service_ids,
        is_active=True,
    ).only(
        "id",
        "slug",
        "name",
        "description",
        "short_description",
        "price_from",
        "duration_min",
        "is_active",
    )

    out: list[dict[str, Any]] = []
    for svc in services:
        category = _derive_category(svc)
        description = (svc.description or svc.short_description or "").strip()
        duration_min = int(svc.duration_min) if svc.duration_min is not None else 0
        out.append(
            {
                "service_id": str(svc.id),
                "name": svc.name,
                "price_rub": _price_rub(svc.price_from),
                "duration_min": duration_min,
                "description": description,
                "category": category,
                "is_active": bool(svc.is_active),
            }
        )

    out.sort(key=lambda d: (d["category"], d["name"]))
    return out


__all__ = [
    "DEFAULT_CATEGORY",
    "list_master_services",
]
