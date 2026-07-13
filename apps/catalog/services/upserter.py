"""Catalog-mirror upserter — Ayla salon-services → CatalogService (S3B / #1044).

Idempotent INSERT-or-UPDATE for the ``CatalogService`` mirror, driven by the
DTOs the Ayla catalog HTTP client (:mod:`apps.catalog.services.http_client`)
produces. Used by the periodic sync orchestrator
(:mod:`apps.catalog.services.sync`).

### Re-key — Decision (S3B PR-1)

The mirror is now keyed on the Ayla **stable UUID** (``ayla_service_id`` =
``SalonService.id``), not the legacy mysite integer ``external_id``. Each row
is an ``update_or_create`` on ``(tenant, ayla_service_id)``. The DB-level
partial ``UniqueConstraint`` (``WHERE ayla_service_id IS NOT NULL``) is the
backstop; the per-tenant Redis sync lock serialises beats so the
``update_or_create`` never races itself.

### Tenant scoping

The sync runs one tenant at a time (the beat fans out per tenant). We wrap
the batch in ``tenant_scope(tenant)`` and write through the tenant-scoped
``.objects`` manager — the mirror-write is intra-tenant by construction, so
it needs neither ``.all_tenants`` nor the marketplace cross-tenant carve-out
(MKT1). ``.objects.update_or_create`` finds the row within the tenant's scope
and stamps/asserts the tenant on create.

### Per-row error isolation

A single malformed DTO must NOT abort the rest of the batch — the sync
orchestrator runs on a schedule and should make forward progress on every
other row. Failures land in :class:`UpsertResult.errors` with the
``ayla_service_id`` + reason; the orchestrator logs them and continues. Each
row is wrapped in a savepoint so its rollback doesn't poison the batch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.db import transaction

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.tenancy.context import tenant_scope

if TYPE_CHECKING:
    from apps.catalog.services.http_client import (
        CatalogSalonServiceDTO,
        CatalogSpecialistDTO,
        CatalogSpecialistServiceDTO,
    )
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@dataclass
class UpsertResult:
    """Counter snapshot a single upsert_* call writes.

    Fields:
      created: rows whose ``(tenant, ayla_service_id)`` wasn't present before.
      updated: rows present + overwritten with the incoming payload.
      skipped: reserved (no conditional skip in the re-key path — kept so the
               orchestrator's counter shape is stable).
      errors: per-row failures with ``{ayla_service_id, reason}`` so the caller
              can blame the right input on a partial batch.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def upsert_salon_services(tenant: "Tenant", dtos: list["CatalogSalonServiceDTO"]) -> UpsertResult:
    """Upsert Ayla salon-services into ``CatalogService`` for one tenant."""
    result = UpsertResult()
    with tenant_scope(tenant), transaction.atomic():
        for dto in dtos:
            try:
                _upsert_one(tenant=tenant, dto=dto, result=result)
            except Exception as exc:  # noqa: BLE001 — per-row safety net
                ayla_id = getattr(dto, "ayla_service_id", "?")
                result.errors.append({"ayla_service_id": ayla_id, "reason": str(exc)})
                logger.exception(
                    "catalog.upsert.row_failed model=CatalogService ayla_service_id=%s",
                    ayla_id,
                )
    return result


def _upsert_one(*, tenant: "Tenant", dto: "CatalogSalonServiceDTO", result: UpsertResult) -> None:
    """One row, wrapped in a savepoint so a failure doesn't poison the batch.

    Runs inside ``tenant_scope(tenant)`` (set by the caller), so the
    tenant-scoped ``.objects`` manager finds the row within this tenant and
    stamps the tenant on create — the explicit ``tenant=`` keeps the lookup
    correct across STRICT_TENANT_SCOPE modes.
    """
    with transaction.atomic():
        _obj, created = CatalogService.objects.update_or_create(
            tenant=tenant,
            ayla_service_id=dto.ayla_service_id,
            defaults=_service_fields(dto),
        )
        if created:
            result.created += 1
        else:
            result.updated += 1


def _service_fields(dto: "CatalogSalonServiceDTO") -> dict[str, Any]:
    """Columnar CatalogService fields the salon-service payload provides.

    ``template``/``category`` have no mirror column yet — they ride in
    ``raw``. Fields with no salon-service source (slug, descriptions, seo_*,
    goals, is_popular, contraindications) keep their model defaults.
    """
    return {
        "external_updated_at": dto.external_updated_at,
        "name": dto.name,
        "is_active": dto.is_active,
        "requires_health_check": dto.requires_health_check,
        "price_from": dto.price_from,
        "duration_min": dto.duration_min,
        "raw": dto.raw,
    }


# ---------------------------------------------------------------------------
# CatalogMaster ← specialist-services identity + /internal/specialists/ name/bio
# ---------------------------------------------------------------------------


def upsert_masters(
    tenant: "Tenant",
    edge_dtos: list["CatalogSpecialistServiceDTO"],
    specialist_dtos: list["CatalogSpecialistDTO"],
) -> UpsertResult:
    """Upsert ``CatalogMaster`` for one tenant, one row per distinct master.

    Master **identity** (``ayla_user_id`` =User.id, ``rating``, ``review_count``)
    comes from the specialist-services **edge** (the contract puts it there so
    no availability-filtered second call is needed). ``name``/``bio`` come from
    the ``/internal/specialists/{id}/`` enrichment when available (a 404 leaves
    them empty — the master is still created from the edge). Keyed on
    ``(tenant, ayla_user_id)``. Platform fields (invite_status, mode, …) are
    never touched. ``external_updated_at`` uses the edge's upstream timestamp.
    """
    name_bio = {d.specialist_id: d for d in specialist_dtos}
    # One master per ayla_user_id — dedupe edges (a specialist offers many
    # services). First edge wins for the freshness timestamp.
    by_user: dict[str, CatalogSpecialistServiceDTO] = {}
    for edge in edge_dtos:
        by_user.setdefault(edge.ayla_user_id, edge)

    result = UpsertResult()
    with tenant_scope(tenant), transaction.atomic():
        for user_id, edge in by_user.items():
            enrich = name_bio.get(edge.specialist)
            try:
                with transaction.atomic():
                    _obj, created = CatalogMaster.objects.update_or_create(
                        tenant=tenant,
                        ayla_user_id=user_id,
                        defaults={
                            "external_updated_at": edge.external_updated_at,
                            "name": enrich.name if enrich else "",
                            "bio": enrich.bio if enrich else "",
                            "rating": edge.rating,
                            "review_count": edge.review_count,
                        },
                    )
                    if created:
                        result.created += 1
                    else:
                        result.updated += 1
            except Exception as exc:  # noqa: BLE001 — per-row safety net
                result.errors.append({"ayla_user_id": user_id, "reason": str(exc)})
                logger.exception(
                    "catalog.upsert.row_failed model=CatalogMaster ayla_user_id=%s",
                    user_id,
                )
    return result


# ---------------------------------------------------------------------------
# MasterService ← specialist-services (the bookable edge)
# ---------------------------------------------------------------------------


def upsert_master_services(
    tenant: "Tenant", dtos: list["CatalogSpecialistServiceDTO"]
) -> UpsertResult:
    """Upsert Ayla specialist-services into ``MasterService`` for one tenant.

    Each edge resolves its ``master`` (CatalogMaster by the edge's
    ``ayla_user_id``) + ``service`` (CatalogService by ``ayla_service_id`` =
    the edge's ``salon_service``), then upserts keyed on the legacy
    ``(tenant, master, service)`` unique — **adopt-and-update**: an admin-matrix
    row for the same pair (bookable id NULL) is backfilled with the Ayla
    bookable id + resolved fields rather than colliding on a blind create. The
    stable ``ayla_specialist_service_id`` is stored (+ its own partial-unique
    backstop). An edge whose master/service mirror row is missing is skipped +
    logged (``result.skipped``), not failed.
    """
    result = UpsertResult()
    with tenant_scope(tenant), transaction.atomic():
        for dto in dtos:
            try:
                with transaction.atomic():
                    _upsert_master_service_one(tenant, dto, result)
            except Exception as exc:  # noqa: BLE001 — per-row safety net
                result.errors.append(
                    {
                        "ayla_specialist_service_id": dto.ayla_specialist_service_id,
                        "reason": str(exc),
                    }
                )
                logger.exception(
                    "catalog.upsert.row_failed model=MasterService bookable=%s",
                    dto.ayla_specialist_service_id,
                )
    return result


def _upsert_master_service_one(
    tenant: "Tenant",
    dto: "CatalogSpecialistServiceDTO",
    result: UpsertResult,
) -> None:
    master = CatalogMaster.objects.filter(ayla_user_id=dto.ayla_user_id).first()
    service = CatalogService.objects.filter(ayla_service_id=dto.salon_service).first()
    if master is None or service is None:
        result.skipped += 1
        logger.warning(
            "catalog.upsert.master_service_skip reason=missing_mirror bookable=%s "
            "master=%s service=%s",
            dto.ayla_specialist_service_id,
            master is not None,
            service is not None,
        )
        return

    # Key on (master, service) — the legacy unique — so an admin-matrix row is
    # adopted-and-updated, not duplicated.
    _obj, created = MasterService.objects.update_or_create(
        tenant=tenant,
        master=master,
        service=service,
        defaults={
            "ayla_specialist_service_id": dto.ayla_specialist_service_id,
            "resolved_duration": dto.resolved_duration,
            "resolved_requires_health_check": dto.resolved_requires_health_check,
            "price": dto.price,
            "is_active": dto.is_active,
        },
    )
    if created:
        result.created += 1
    else:
        result.updated += 1
