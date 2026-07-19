"""Link legacy ``CatalogService`` rows to Ayla service ids (C6 / AMD-001).

Back-fill engine behind the ``link_ayla_service_ids`` management command.

### The C6 contract (PILOT_CONTRACTS_2026-08-15 §14 AMD-001)

``ServiceTemplate`` has no slug field in the pilot, so matching keys off
the **pair** ``(category_slug, normalized name)`` — the Ayla internal
mirror exposes both raw (W1, ``e988dfb9``); normalization is bot-side:
lower (casefold), trim/collapse whitespace, ё→е, strip «ёлочки».
**Duration** is the tiebreaker when a pair is ambiguous (unique match
only). A **mapping file** (JSON, ``ayla template_id | ayla_service_id ↔
bot service``) carries manual correspondences for rows that cannot
auto-match. Coverage report buckets: ``matched auto`` /
``matched manual`` / ``unmatched``.

Bot-side category slug: the mirror carries no dedicated category column
on ``CatalogService`` — the row's ``slug`` plays that role (pilot salon
catalog rows are category-level services: ``lpg-massage``, ``body-wrap``).
Rows whose real category diverges from ``slug`` land in the mapping
file — that is exactly what the manual channel is for.

### Why this exists

The ``BOOKING_VIA_AYLA_REST`` health-check gate grounds on
``ayla_service_id`` and fails closed on a miss (#1016 / #1034), so the
Penza pilot services must be ~100% linked before the flip. Ayla-fed
rows (S3B sync) are keyed on ``ayla_service_id`` by construction — this
module only touches the unlinked legacy remainder.

### Duplicates

If the matched ``ayla_service_id`` already sits on another row of the
same tenant (the S3B sync created the Ayla-keyed twin), stamping would
violate ``uq_catalog_service_tenant_ayla_service_id``. The row is
reported as a **duplicate**; with ``deactivate_duplicates=True`` the
legacy twin is retired (``is_active=False``).
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


# Mapping-file value shape: {"tenant_slug": ..., "service_slug": ...}.
# Keys are ayla_service_id OR ayla template_id (C6 discovery key).
ManualMap = dict[str, dict[str, str]]


@dataclass(frozen=True)
class LinkAction:
    """A row whose ``ayla_service_id`` was (or would be) stamped."""

    service_pk: int
    slug: str
    name: str
    matched_by: str  # "pair" | "pair+duration" | "manual"
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
    matched_auto: list[LinkAction] = field(default_factory=list)
    matched_manual: list[LinkAction] = field(default_factory=list)
    unmatched: list[UnmatchedRow] = field(default_factory=list)
    duplicates: list[DuplicateAction] = field(default_factory=list)
    manual_skipped: list[str] = field(default_factory=list)

    @property
    def coverage_after(self) -> float:
        """Projected coverage once links/retirements are applied."""
        retired = sum(1 for d in self.duplicates if d.deactivated)
        active = self.active_before - retired
        if active <= 0:
            return 1.0
        covered = self.covered_before + len(self.matched_auto) + len(self.matched_manual)
        return min(covered / active, 1.0)


def normalize_name(name: str) -> str:
    """C6 normalization: strip «ёлочки», casefold, ё→е, collapse spaces."""
    cleaned = name.replace("«", " ").replace("»", " ")
    return " ".join(cleaned.casefold().replace("ё", "е").split())


def extract_template_slug(dto: "CatalogSalonServiceDTO") -> str | None:
    """Best-effort template slug from the wire payload (forward-compat).

    C6 froze «no ServiceTemplate.slug in the pilot», but the extraction
    stays cheap insurance for a post-pilot additive contract bump:
    ``template_slug``, top-level ``slug``, or ``template`` as an object
    with ``slug``. Anything else → None.
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


@dataclass(frozen=True)
class _Candidate:
    ayla_service_id: str
    duration_min: int | None
    template_id: str | None


def _build_pair_index(
    dtos: list["CatalogSalonServiceDTO"],
) -> dict[tuple[str, str], list[_Candidate]]:
    """(norm category, norm name) → candidate rows (may be several)."""
    index: dict[tuple[str, str], list[_Candidate]] = {}
    for dto in dtos:
        pair = (normalize_name(dto.category or ""), normalize_name(dto.name))
        if not pair[1]:
            continue
        index.setdefault(pair, []).append(
            _Candidate(
                ayla_service_id=dto.ayla_service_id,
                duration_min=dto.duration_min,
                template_id=_template_id_of(dto),
            )
        )
    return index


def _template_id_of(dto: "CatalogSalonServiceDTO") -> str | None:
    """Ayla template_id (ServiceTemplate UUID) from the DTO, if any."""
    if dto.template:
        return str(dto.template)
    raw = dto.raw or {}
    template = raw.get("template")
    if isinstance(template, dict) and template.get("id"):
        return str(template["id"])
    return None


def link_tenant_services(
    tenant: "Tenant",
    dtos: list["CatalogSalonServiceDTO"],
    *,
    apply: bool,
    deactivate_duplicates: bool = False,
    manual_map: ManualMap | None = None,
) -> TenantLinkReport:
    """Match one tenant's unlinked rows against Ayla ``dtos`` (C6).

    Order per row: manual mapping file first (explicit human truth),
    then the auto pair/duration matcher. Always computes the full plan;
    writes only when ``apply`` is true. Retiring duplicates additionally
    requires ``deactivate_duplicates``.
    """
    pair_index = _build_pair_index(dtos)
    manual_map = manual_map or {}

    # ayla key (service id OR template id) → service id, for manual entries.
    key_to_service_id: dict[str, str] = {}
    for dto in dtos:
        key_to_service_id[str(dto.ayla_service_id)] = str(dto.ayla_service_id)
        tid = _template_id_of(dto)
        if tid:
            key_to_service_id.setdefault(tid, str(dto.ayla_service_id))

    report = TenantLinkReport(tenant_slug=tenant.slug, active_before=0, covered_before=0)

    with tenant_scope(tenant):
        active_qs = CatalogService.objects.filter(is_active=True)
        report.active_before = active_qs.count()
        report.covered_before = active_qs.filter(ayla_service_id__isnull=False).count()
        taken_ids: dict[str, int] = {
            str(row.ayla_service_id): row.pk
            for row in CatalogService.objects.filter(ayla_service_id__isnull=False)
        }

        candidates = active_qs.filter(ayla_service_id__isnull=True).order_by("name")
        for row in candidates:
            ayla_id, matched_by = _match(
                row,
                pair_index=pair_index,
                manual_map=manual_map,
                key_to_service_id=key_to_service_id,
                tenant_slug=tenant.slug,
                report=report,
            )
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
            action = LinkAction(
                service_pk=row.pk,
                slug=row.slug,
                name=row.name,
                matched_by=matched_by,
                ayla_service_id=ayla_id,
            )
            if matched_by == "manual":
                report.matched_manual.append(action)
            else:
                report.matched_auto.append(action)
    return report


def _match(
    row: CatalogService,
    *,
    pair_index: dict[tuple[str, str], list[_Candidate]],
    manual_map: ManualMap,
    key_to_service_id: dict[str, str],
    tenant_slug: str,
    report: TenantLinkReport,
) -> tuple[str | None, str]:
    """Manual mapping first, then pair+duration. Never guess."""
    # 1. Manual mapping file — explicit human correspondence. A target
    # matching THIS row whose ayla key resolves to nothing upstream is a
    # dead entry: reported, never stamped blindly.
    for ayla_key, target in manual_map.items():
        if target.get("tenant_slug") == tenant_slug and target.get("service_slug") == row.slug:
            service_id = key_to_service_id.get(str(ayla_key))
            if service_id is not None:
                return service_id, "manual"
            report.manual_skipped.append(str(ayla_key))
    # 2. Auto pair match.
    pair = (normalize_name(row.slug or ""), normalize_name(row.name))
    candidates = pair_index.get(pair, [])
    if len(candidates) == 1:
        return candidates[0].ayla_service_id, "pair"
    if len(candidates) > 1 and row.duration_min is not None:
        by_duration = [c for c in candidates if c.duration_min == row.duration_min]
        if len(by_duration) == 1:
            return by_duration[0].ayla_service_id, "pair+duration"
    return None, ""
