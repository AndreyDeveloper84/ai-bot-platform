"""Link legacy ``CatalogService`` rows to Ayla service ids (S1-B).

Back-fill engine behind the ``link_ayla_service_ids`` management command.

### Why this exists

Pre-S3B catalog rows carry a legacy integer ``external_id`` and leave
``ayla_service_id`` NULL. The ``BOOKING_VIA_AYLA_REST`` health-check gate
grounds on that column and fails closed on a miss (#1016 / #1034), so the
pilot needs ~100% of the Penza salon's active services linked before the
flip. Ayla-fed rows (written by the S3B sync) are keyed on
``ayla_service_id`` by construction — this module only touches the
unlinked remainder.

### Matching rules (deliberately simple)

1. **Slug** — exact match against the Ayla template slug, when the wire
   provides one (forward-compat: ``template_slug`` key, top-level
   ``slug``, or ``template`` as an object with ``slug``; W1 ships the slug
   per #200 t5). Ambiguous slugs (several Ayla rows sharing one) are
   dropped from the index.
2. **Name** — normalized exact match (casefold, collapsed whitespace,
   ё→е). Only unambiguous names are used.
3. Otherwise the row is reported **unmatched** — never guessed.

### Duplicates

If the matched ``ayla_service_id`` already sits on another row of the
same tenant (the S3B sync already created the Ayla-keyed twin), stamping
it would violate the partial unique constraint
``uq_catalog_service_tenant_ayla_service_id``. The row is reported as a
**duplicate**; with ``deactivate_duplicates=True`` the legacy twin is
retired (``is_active=False``) so coverage reflects the canonical row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apps.catalog.models import CatalogService
from apps.tenancy.context import tenant_scope

if TYPE_CHECKING:
    from apps.catalog.services.http_client import CatalogSalonServiceDTO
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkAction:
    """A row whose ``ayla_service_id`` was (or would be) stamped."""

    service_pk: int
    slug: str
    name: str
    matched_by: str  # "slug" | "name"
    ayla_service_id: str


@dataclass(frozen=True)
class DuplicateAction:
    """A row whose match already lives on another (Ayla-keyed) row."""

    service_pk: int
    slug: str
    name: str
    ayla_service_id: str
    existing_pk: int
    deactivated: bool


@dataclass(frozen=True)
class UnmatchedRow:
    """A row no Ayla service could be matched to."""

    service_pk: int
    slug: str
    name: str


@dataclass
class TenantLinkReport:
    """Outcome for one tenant. Counts are projected even in dry-run."""

    tenant_slug: str
    active_before: int
    covered_before: int
    links: list[LinkAction] = field(default_factory=list)
    duplicates: list[DuplicateAction] = field(default_factory=list)
    unmatched: list[UnmatchedRow] = field(default_factory=list)

    @property
    def coverage_after(self) -> float:
        """Projected coverage once links/retirements are applied.

        Denominator shrinks by retired duplicates (they leave the active
        set), numerator grows by stamped links.
        """
        retired = sum(1 for d in self.duplicates if d.deactivated)
        active = self.active_before - retired
        if active <= 0:
            return 1.0
        covered = self.covered_before + len(self.links)
        return min(covered / active, 1.0)


def _normalize_name(name: str) -> str:
    """Casefold + collapse whitespace + ё→е (Russian catalogue hazard)."""
    return " ".join(name.casefold().replace("ё", "е").split())


def extract_template_slug(dto: "CatalogSalonServiceDTO") -> str | None:
    """Best-effort template slug from the wire payload (forward-compat).

    Today's payload carries ``template`` as a bare UUID; W1 adds the slug
    (#200 t5). Accept, in order: ``template_slug``, top-level ``slug``,
    ``template`` as an object with ``slug``. Anything else → None.
    """
    raw = dto.raw or {}
    for key in ("template_slug", "slug"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    template = raw.get("template")
    if isinstance(template, dict):
        value = template.get("slug")
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return None


def _build_indexes(
    dtos: list["CatalogSalonServiceDTO"],
) -> tuple[dict[str, str], dict[str, str]]:
    """Unambiguous-match indexes: slug → ayla_id, norm-name → ayla_id.

    Entries seen more than once are evicted — an ambiguous key must never
    produce a guess.
    """
    slug_counts: dict[str, str | None] = {}
    name_counts: dict[str, str | None] = {}
    for dto in dtos:
        slug = extract_template_slug(dto)
        if slug:
            if slug not in slug_counts:
                slug_counts[slug] = dto.ayla_service_id
            elif slug_counts[slug] != dto.ayla_service_id:
                slug_counts[slug] = None
        name = _normalize_name(dto.name)
        if name:
            if name not in name_counts:
                name_counts[name] = dto.ayla_service_id
            elif name_counts[name] != dto.ayla_service_id:
                name_counts[name] = None
    return (
        {k: v for k, v in slug_counts.items() if v},
        {k: v for k, v in name_counts.items() if v},
    )


def link_tenant_services(
    tenant: "Tenant",
    dtos: list["CatalogSalonServiceDTO"],
    *,
    apply: bool,
    deactivate_duplicates: bool = False,
) -> TenantLinkReport:
    """Match one tenant's unlinked rows against Ayla ``dtos``.

    Always computes the full plan; writes only when ``apply`` is true.
    Retiring duplicates additionally requires ``deactivate_duplicates``.
    """
    slug_index, name_index = _build_indexes(dtos)
    taken_ids: dict[str, int] = {}  # ayla_service_id → existing row pk

    report = TenantLinkReport(tenant_slug=tenant.slug, active_before=0, covered_before=0)

    with tenant_scope(tenant):
        active_qs = CatalogService.objects.filter(is_active=True)
        report.active_before = active_qs.count()
        report.covered_before = active_qs.filter(ayla_service_id__isnull=False).count()
        # Any row already carrying a link blocks stamping the same id on a
        # twin (unique constraint is active-state-agnostic), so scan all.
        taken_ids = {
            str(row.ayla_service_id): row.pk
            for row in CatalogService.objects.filter(ayla_service_id__isnull=False)
        }

        candidates = active_qs.filter(ayla_service_id__isnull=True).order_by("name")
        for row in candidates:
            ayla_id, matched_by = _match(row, slug_index, name_index)
            if ayla_id is None:
                report.unmatched.append(
                    UnmatchedRow(service_pk=row.pk, slug=row.slug, name=row.name)
                )
                continue
            existing_pk = taken_ids.get(ayla_id)
            if existing_pk is not None:
                deactivated = False
                if apply and deactivate_duplicates:
                    row.is_active = False
                    row.save(update_fields=["is_active", "synced_at"])
                    deactivated = True
                    logger.info(
                        "catalog.link.duplicate_deactivated pk=%s ayla_service_id=%s",
                        row.pk,
                        ayla_id,
                    )
                report.duplicates.append(
                    DuplicateAction(
                        service_pk=row.pk,
                        slug=row.slug,
                        name=row.name,
                        ayla_service_id=ayla_id,
                        existing_pk=existing_pk,
                        deactivated=deactivated,
                    )
                )
                continue
            if apply:
                row.ayla_service_id = ayla_id
                row.save(update_fields=["ayla_service_id", "synced_at"])
                taken_ids[ayla_id] = row.pk
                logger.info(
                    "catalog.link.linked pk=%s by=%s ayla_service_id=%s",
                    row.pk,
                    matched_by,
                    ayla_id,
                )
            report.links.append(
                LinkAction(
                    service_pk=row.pk,
                    slug=row.slug,
                    name=row.name,
                    matched_by=matched_by,
                    ayla_service_id=ayla_id,
                )
            )
    return report


def _match(
    row: CatalogService,
    slug_index: dict[str, str],
    name_index: dict[str, str],
) -> tuple[str | None, str]:
    """Slug first, then normalized name. Never guess."""
    slug = (row.slug or "").strip().casefold()
    if slug and slug in slug_index:
        return slug_index[slug], "slug"
    name = _normalize_name(row.name)
    if name and name in name_index:
        return name_index[name], "name"
    return None, ""
