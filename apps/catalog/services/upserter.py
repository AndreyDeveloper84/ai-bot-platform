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

from apps.catalog.models import CatalogService
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
      skipped: rows deliberately not written — an edge whose master/service
               isn't mirrored yet, or one carrying a foreign tenant.
      deactivated: sync-owned rows reconciled to ``is_active=False`` because
               they vanished from the upstream snapshot (master-service only).
      errors: per-row failures with ``{ayla_service_id, reason}`` so the caller
              can blame the right input on a partial batch.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    deactivated: int = 0
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


def upsert_specialists(tenant: "Tenant", dtos: list["CatalogSpecialistDTO"]) -> UpsertResult:
    """Upsert Ayla specialists into ``CatalogMaster`` for one tenant (S3B masters).

    Keyed by the canonical Ayla SpecialistProfile.id (``CatalogMaster.id``).
    Update overwrites ONLY mirror fields (name, bio, experience, rating,
    review_count, is_active, ayla_user_id, external_updated_at, raw) —
    platform-owned fields (invite_status, mode, photo_url, archived_at,
    invited_at, max_handle, linked_bot_user) are NEVER touched by sync.

    Missing-from-feed rows are kept as-is (same policy as salon-services:
    upsert-only, no proactive deactivation — documented in the S3B PR
    report). Per-row error isolation matches the services path.
    """
    from apps.catalog.models import CatalogMaster

    result = UpsertResult()
    with tenant_scope(tenant), transaction.atomic():
        for dto in dtos:
            try:
                with transaction.atomic():
                    _obj, created = CatalogMaster.objects.update_or_create(
                        tenant=tenant,
                        id=dto.ayla_master_id,
                        defaults={
                            "name": dto.name,
                            "bio": dto.bio,
                            "experience": dto.experience,
                            "rating": dto.rating,
                            "review_count": dto.review_count,
                            "is_active": dto.is_active,
                            "ayla_user_id": dto.user_id,
                            "external_updated_at": dto.external_updated_at,
                            "raw": dto.raw,
                        },
                    )
                    if created:
                        result.created += 1
                    else:
                        result.updated += 1
            except Exception as exc:  # noqa: BLE001 — per-row safety net
                ayla_id = getattr(dto, "ayla_master_id", "?")
                result.errors.append({"ayla_master_id": ayla_id, "reason": str(exc)})
                logger.exception(
                    "catalog.upsert.row_failed model=CatalogMaster ayla_master_id=%s",
                    ayla_id,
                )
    return result


def upsert_master_services(
    tenant: "Tenant",
    dtos: list["CatalogSpecialistServiceDTO"],
    *,
    reconcile: bool = True,
) -> UpsertResult:
    """Mirror Ayla's bookable master↔service edges into ``MasterService`` (DRF-945).

    This is what makes service-specific discovery possible: without it,
    ``CatalogMaster.specialization`` (which no sync path populates) is the only
    thing ``discover_masters`` can filter on, so every service-specific query
    returns zero.

    ### What sync may touch

    ``MasterService`` has two writers — the operator (MM4 matrix / invite
    seeding) and this function. Ownership is discriminated by
    ``ayla_specialist_service_id``: NULL ⇒ operator's, non-NULL ⇒ sync's.
    Reconciliation is scoped to sync-owned rows, so an operator mapping is
    never deactivated by a beat.

    An operator row for a pair Ayla also publishes is **adopted** (stamped with
    the Ayla id) rather than duplicated — ``(master, service)`` is unique, and
    a duplicate would be both an IntegrityError and a double discovery card.

    ### Reconciliation

    ``?tenant=`` makes the fetch a complete per-tenant snapshot, so any owned
    row absent from it is stale and gets ``is_active=False`` — deactivate, not
    delete, because the MM4 matrix reads row existence.

    ``reconcile=False`` is how the caller says "this snapshot is not
    trustworthy" (partial upstream failure). An **empty** snapshot is treated
    the same way and self-vetoes: wiping a tenant's whole edge set on what is
    far more likely a tenant-id mismatch would silently re-create the exact
    zero-result bug this mirror exists to fix.
    """
    from apps.catalog.models import CatalogMaster, CatalogService, MasterService

    result = UpsertResult()
    seen_ayla_ids: set[str] = set()

    with tenant_scope(tenant), transaction.atomic():
        for dto in dtos:
            try:
                with transaction.atomic():
                    written = _upsert_one_master_service(
                        tenant=tenant,
                        dto=dto,
                        result=result,
                        master_model=CatalogMaster,
                        service_model=CatalogService,
                        edge_model=MasterService,
                    )
                if written:
                    seen_ayla_ids.add(dto.ayla_specialist_service_id)
            except Exception as exc:  # noqa: BLE001 — per-row safety net
                ayla_id = getattr(dto, "ayla_specialist_service_id", "?")
                result.errors.append({"ayla_specialist_service_id": ayla_id, "reason": str(exc)})
                logger.exception(
                    "catalog.upsert.row_failed model=MasterService ayla_specialist_service_id=%s",
                    ayla_id,
                )

        if reconcile:
            result.deactivated = _reconcile_master_services(
                tenant=tenant,
                seen_ayla_ids=seen_ayla_ids,
                had_errors=bool(result.errors),
                edge_model=MasterService,
            )

    return result


def _upsert_one_master_service(
    *,
    tenant: "Tenant",
    dto: "CatalogSpecialistServiceDTO",
    result: UpsertResult,
    master_model: Any,
    service_model: Any,
    edge_model: Any,
) -> bool:
    """Write one edge. Returns True when a row was created/updated.

    False means "deliberately skipped" — the edge is unmappable right now
    (master or service not mirrored yet, or a foreign tenant). A skipped edge
    is NOT added to the seen-set, so it cannot be mistaken for a live edge; but
    it also must not deactivate anything, which is why the caller only
    reconciles against edges it actually resolved.
    """
    # Cross-tenant guard #1 — the payload's own tenant must match the tenant
    # we're syncing. Ayla denormalizes `tenant` onto the edge; a mismatch means
    # the upstream filter misfired and we must not write it.
    if dto.tenant and dto.tenant != str(tenant.id):
        result.skipped += 1
        logger.warning(
            "catalog.upsert.skipped model=MasterService reason=foreign_tenant "
            "ayla_specialist_service_id=%s payload_tenant=%s sync_tenant=%s",
            dto.ayla_specialist_service_id,
            dto.tenant,
            tenant.id,
        )
        return False

    # Cross-tenant guard #2 — both lookups go through the tenant-scoped
    # ``.objects`` manager under ``tenant_scope(tenant)``, so a master or
    # service belonging to another tenant simply isn't found. A cross-tenant
    # edge is therefore unrepresentable, not merely rejected.
    master = master_model.objects.filter(id=dto.specialist).first()
    if master is None:
        result.skipped += 1
        logger.info(
            "catalog.upsert.skipped model=MasterService reason=unknown_master "
            "specialist=%s tenant_id=%s",
            dto.specialist,
            tenant.id,
        )
        return False

    service = service_model.objects.filter(ayla_service_id=dto.salon_service).first()
    if service is None:
        result.skipped += 1
        logger.info(
            "catalog.upsert.skipped model=MasterService reason=unknown_service "
            "salon_service=%s tenant_id=%s",
            dto.salon_service,
            tenant.id,
        )
        return False

    # Defensive un-stamp: if this Ayla edge id currently sits on a DIFFERENT
    # local pair (upstream re-parented it), release it first. Without this the
    # partial unique constraint would reject the write on every beat forever.
    edge_model.objects.filter(
        tenant=tenant,
        ayla_specialist_service_id=dto.ayla_specialist_service_id,
    ).exclude(master=master, service=service).update(ayla_specialist_service_id=None)

    _obj, created = edge_model.objects.update_or_create(
        tenant=tenant,
        master=master,
        service=service,
        defaults={
            "ayla_specialist_service_id": dto.ayla_specialist_service_id,
            "is_active": dto.is_active,
        },
    )
    if created:
        result.created += 1
    else:
        result.updated += 1
    return True


def _reconcile_master_services(
    *,
    tenant: "Tenant",
    seen_ayla_ids: set[str],
    had_errors: bool,
    edge_model: Any,
) -> int:
    """Deactivate sync-owned edges missing from the snapshot. Returns the count.

    Self-vetoes on an empty seen-set or a partially failed batch — both mean
    the snapshot can't be trusted to prove absence, and requirement 8 says a
    partial upstream failure must never leave behind knowingly-false state
    (deactivating a live edge is exactly that).
    """
    owned = edge_model.objects.filter(
        tenant=tenant,
        ayla_specialist_service_id__isnull=False,
        is_active=True,
    )

    if not seen_ayla_ids:
        if owned.exists():
            logger.warning(
                "catalog.reconcile.vetoed model=MasterService reason=empty_snapshot "
                "tenant_id=%s owned_active=%d — refusing to deactivate every edge; "
                "verify the Ayla ?tenant= filter and Tenant.id mapping.",
                tenant.id,
                owned.count(),
            )
        return 0

    if had_errors:
        logger.warning(
            "catalog.reconcile.vetoed model=MasterService reason=partial_batch "
            "tenant_id=%s — snapshot incomplete, skipping deactivation.",
            tenant.id,
        )
        return 0

    stale = owned.exclude(ayla_specialist_service_id__in=seen_ayla_ids)
    count = stale.update(is_active=False)
    if count:
        logger.info(
            "catalog.reconcile.deactivated model=MasterService tenant_id=%s count=%d",
            tenant.id,
            count,
        )
    return count
