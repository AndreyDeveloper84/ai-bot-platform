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

from django.db import IntegrityError, transaction

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
               isn't mirrored yet, one carrying a foreign tenant, or a pair an
               operator already owns.
      removed: sync-owned rows deleted because upstream no longer offers them
               — vanished from the snapshot, or marked inactive. This is the
               destructive-action signal the beat log surfaces, so a row that
               merely MOVED is deliberately not counted here.
      reparented: sync-owned rows deleted because upstream moved the edge to a
               different (master, service) pair. The row is immediately
               re-created at the new pair, so nothing is actually lost.
      errors: per-row failures with ``{ayla_service_id, reason}`` so the caller
              can blame the right input on a partial batch.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    reparented: int = 0
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
    is_popular, contraindications) keep their model defaults.

    ``goals`` is written from the feed since DRF-1308. Before that it had no
    source at all and stayed ``[]`` on every row, which is why the goal
    layer was invisible to this platform end to end. Sync overwrites it
    wholesale: goals are curated on the Ayla side, so an emptied list
    upstream must empty the mirror too — otherwise a retracted goal would
    live on here forever.
    """
    return {
        "external_updated_at": dto.external_updated_at,
        "name": dto.name,
        "is_active": dto.is_active,
        "requires_health_check": dto.requires_health_check,
        "price_from": dto.price_from,
        "duration_min": dto.duration_min,
        "goals": dto.goals,
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

    ### Cross-tenant guard (DRF-1313)

    ``fetch_specialists`` now sends ``?tenant=``, so the feed should already be
    this tenant's masters and nobody else's. Should. The defect this guard
    exists for was a filter that silently did not apply, and the cost of
    trusting it was three of five pilot salons becoming unbookable — so the
    payload's own ``tenant`` is re-checked here before anything is written,
    exactly as :func:`upsert_master_services` already does for edges.

    A DTO with no ``tenant`` (an Ayla that predates the field) is not a
    mismatch and is not blocked: it is simply unverifiable, and blocking it
    would turn a deploy-order skew into an outage.
    """
    from apps.catalog.models import CatalogMaster

    result = UpsertResult()
    with tenant_scope(tenant), transaction.atomic():
        for dto in dtos:
            if dto.tenant and dto.tenant != str(tenant.id):
                result.skipped += 1
                logger.warning(
                    "catalog.upsert.skipped model=CatalogMaster reason=foreign_tenant "
                    "ayla_master_id=%s payload_tenant=%s sync_tenant=%s — the upstream "
                    "?tenant= filter did not hold; writing this would attribute another "
                    "salon's master to this one (DRF-1313).",
                    dto.ayla_master_id,
                    dto.tenant,
                    tenant.id,
                )
                continue
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
            except IntegrityError as exc:
                # ``CatalogMaster.id`` is the global PK, so one Ayla master can
                # exist under exactly one tenant at a time. Rows mis-attributed
                # by the tenant-blind pull therefore keep the id hostage: the
                # corrected sync cannot create the master under its real salon
                # while the wrong salon still holds the row, and it fails here
                # every beat until someone removes it.
                #
                # This branch does not repair anything — deleting or re-parenting
                # live mirror rows is an owner decision, not a side effect of a
                # sync beat. It exists so the failure names the row and says what
                # has to happen, instead of surfacing as an opaque duplicate-key
                # error once per beat forever.
                #
                # Who holds the row is deliberately NOT looked up: that would be
                # a cross-tenant catalog read, which only apps/marketplace may do
                # (MKT1). The inference needs no such read. ``update_or_create``
                # only reaches an INSERT when the tenant-scoped ``get()`` found
                # nothing, so a row still invisible in this tenant's scope after
                # a unique violation means the id is taken outside it.
                ayla_id = getattr(dto, "ayla_master_id", "?")
                if CatalogMaster.objects.filter(pk=ayla_id).exists():
                    result.errors.append({"ayla_master_id": ayla_id, "reason": str(exc)})
                    logger.exception(
                        "catalog.upsert.row_failed model=CatalogMaster ayla_master_id=%s",
                        ayla_id,
                    )
                    continue
                result.errors.append(
                    {"ayla_master_id": ayla_id, "reason": "held_by_other_tenant"},
                )
                logger.error(
                    "catalog.upsert.master_held_by_other_tenant model=CatalogMaster "
                    "ayla_master_id=%s name=%r sync_tenant=%s — this id already exists "
                    "under a different tenant, which is what a tenant-blind pull leaves "
                    "behind (DRF-1313). The mis-attributed mirror row must be removed or "
                    "re-parented before this salon can mirror its own master; sync will "
                    "not do it.",
                    ayla_id,
                    dto.name,
                    tenant.id,
                )
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
    **Sync only ever touches rows it created.** An operator row for a pair Ayla
    also publishes is deliberately NOT adopted: adoption would put an
    operator-authored row under sync's reconciliation, and the operator's row
    already states the same fact, so there is nothing to gain and a row to lose.

    ### Presence is the contract

    Row existence — not a flag — means "this master performs this service".
    That is what every existing reader already assumes (booking create,
    reschedule, slots, master/miniapp catalogs, the MM4 matrix); none of them
    filters a status column. So an edge Ayla marks ``is_active=False`` must
    leave **no row**, and a vanished edge must be **deleted**, not tombstoned.
    A tombstone would read as "offered" to all seven readers and would make a
    non-bookable service bookable — a regression this mirror must not
    introduce. Delete is also the table's canonical lifecycle: the MM4 matrix
    deletes the row when an operator unchecks a cell.

    ### Reconciliation

    ``?tenant=`` makes the fetch a complete per-tenant snapshot, so a sync-owned
    row absent from it is stale and is deleted.

    Deletion is gated on the snapshot being able to *prove* absence. It
    self-vetoes when it cannot:

    * ``reconcile=False`` — caller says the snapshot is untrustworthy;
    * empty snapshot — far likelier a tenant-id mismatch than a real wipe, and
      wiping the tenant's edges would silently re-create the exact zero-result
      bug this mirror exists to fix;
    * any row error in the batch.

    Edges that were **skipped** (unresolvable master/service, foreign tenant)
    are protected explicitly: a skip proves nothing about upstream absence, so
    the row it would have refreshed must survive. Without that, a beat in which
    N edges fail to resolve would delete N live relations.
    """
    from apps.catalog.models import CatalogMaster, CatalogService, MasterService

    result = UpsertResult()
    seen_ayla_ids: set[str] = set()
    protected_ayla_ids: set[str] = set()

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
                else:
                    # Skipped ≠ absent upstream. Protect whatever row this edge
                    # id owns so reconciliation cannot mistake an unresolvable
                    # edge for a deleted one.
                    protected_ayla_ids.add(dto.ayla_specialist_service_id)
            except Exception as exc:  # noqa: BLE001 — per-row safety net
                ayla_id = getattr(dto, "ayla_specialist_service_id", "?")
                protected_ayla_ids.add(ayla_id)
                result.errors.append({"ayla_specialist_service_id": ayla_id, "reason": str(exc)})
                logger.exception(
                    "catalog.upsert.row_failed model=MasterService ayla_specialist_service_id=%s",
                    ayla_id,
                )

        if reconcile:
            result.removed += _reconcile_master_services(
                tenant=tenant,
                seen_ayla_ids=seen_ayla_ids,
                protected_ayla_ids=protected_ayla_ids,
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
    """Write one edge. Returns True when the edge was fully resolved.

    False means "deliberately skipped" — the edge is unmappable right now
    (master or service not mirrored yet, or a foreign tenant). The caller
    protects skipped edge ids from reconciliation: a skip says nothing about
    whether the edge still exists upstream, so deleting on that basis would
    destroy live relations.

    An upstream ``is_active=False`` edge resolves normally but leaves **no**
    row (removing a sync-owned one if present) — presence is the contract, and
    no reader filters a status column.
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
    # service owned by another tenant simply isn't found and the edge is
    # skipped rather than mis-written.
    #
    # Both lookups are now equally strong: salon-services and specialists are
    # each fetched with ``?tenant=`` (DRF-1313 closed the masters half), and
    # ``upsert_specialists`` re-checks the payload's tenant before writing, so
    # a foreign CatalogService or CatalogMaster is never mirrored here in the
    # first place. Guard #1 stays regardless — the edge asserting its own
    # tenant is the check that does not depend on any other mirror having been
    # correct first.
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

    # Re-parent: if this Ayla edge id currently sits on a DIFFERENT local pair
    # (upstream moved it), that old row is stale AND provably sync-owned — it
    # carries our stamp. Delete it. Releasing the stamp instead would leave a
    # NULL-stamped row that is indistinguishable from an operator row, so
    # reconciliation could never reclaim it: a permanent false relation.
    # Skipping this entirely would wedge the beat forever on the partial
    # unique constraint.
    reparented = (
        edge_model.objects.filter(
            tenant=tenant,
            ayla_specialist_service_id=dto.ayla_specialist_service_id,
        )
        .exclude(master=master, service=service)
        .delete()
    )
    if reparented[0]:
        result.reparented += reparented[0]
        logger.info(
            "catalog.upsert.reparented model=MasterService "
            "ayla_specialist_service_id=%s removed=%d tenant_id=%s",
            dto.ayla_specialist_service_id,
            reparented[0],
            tenant.id,
        )

    existing = edge_model.objects.filter(tenant=tenant, master=master, service=service).first()

    # An operator row already asserts this pair. Leave it completely alone —
    # adopting it would hand an operator-authored row to reconciliation, and it
    # already carries the same meaning. Nothing to write, nothing to own.
    if existing is not None and existing.ayla_specialist_service_id is None:
        result.skipped += 1
        logger.debug(
            "catalog.upsert.skipped model=MasterService reason=operator_owned "
            "master=%s service=%s tenant_id=%s",
            master.pk,
            service.pk,
            tenant.id,
        )
        return False

    if not dto.is_active:
        # Not offered upstream ⇒ no row. Presence is the contract.
        if existing is not None:
            existing.delete()
            result.removed += 1
        else:
            result.skipped += 1
        return True

    if existing is None:
        edge_model.objects.create(
            tenant=tenant,
            master=master,
            service=service,
            ayla_specialist_service_id=dto.ayla_specialist_service_id,
        )
        result.created += 1
        return True

    # NOTE: ``updated`` here means "present and verified correct after this
    # beat", which includes the no-save path below — unlike the other two
    # mirrors where it strictly means "written".
    #
    # Already correct — do NOT save. ``updated_at`` is ``auto_now``, and the
    # MM4 matrix derives its optimistic-concurrency token from
    # MAX(updated_at) across the tenant's rows. An unconditional save would
    # bump that token every beat and 409 any operator mid-edit.
    if str(existing.ayla_specialist_service_id) != dto.ayla_specialist_service_id:
        existing.ayla_specialist_service_id = dto.ayla_specialist_service_id
        existing.save(update_fields=["ayla_specialist_service_id", "updated_at"])
    result.updated += 1
    return True


def _reconcile_master_services(
    *,
    tenant: "Tenant",
    seen_ayla_ids: set[str],
    protected_ayla_ids: set[str],
    had_errors: bool,
    edge_model: Any,
) -> int:
    """Delete sync-owned edges the snapshot proves are gone. Returns the count.

    ``ayla_specialist_service_id__isnull=False`` is the whole safety story for
    co-ownership: operator rows carry NULL and are therefore outside this
    queryset by construction.

    Self-vetoes whenever the snapshot cannot prove absence — an empty snapshot
    or a partially failed batch. Requirement 8: a partial upstream failure must
    never leave knowingly-false state behind, and deleting a live relation on
    incomplete evidence is exactly that.

    ``protected_ayla_ids`` carries edges that were present upstream but could
    not be resolved locally this beat. They are emphatically NOT absent, so
    they must survive; without this a beat where N edges fail to resolve would
    delete N live relations.
    """
    # LOAD-BEARING: ``ayla_specialist_service_id__isnull=False`` is the single
    # guard between this delete and every operator-owned row in the tenant.
    # Removing it deletes the entire MM4 matrix. (Django renders the exclude
    # below as ``NOT (f IN (…) AND f IS NOT NULL)``, so NULL-stamped rows would
    # survive the exclude and land in the delete.)
    owned = edge_model.objects.filter(
        tenant=tenant,
        ayla_specialist_service_id__isnull=False,
    )

    if not seen_ayla_ids:
        if owned.exists():
            logger.warning(
                "catalog.reconcile.vetoed model=MasterService reason=empty_snapshot "
                "tenant_id=%s owned=%d — refusing to delete every edge; verify the "
                "Ayla ?tenant= filter and Tenant.id mapping.",
                tenant.id,
                owned.count(),
            )
        return 0

    if had_errors:
        logger.warning(
            "catalog.reconcile.vetoed model=MasterService reason=partial_batch "
            "tenant_id=%s — snapshot incomplete, skipping deletion.",
            tenant.id,
        )
        return 0

    keep = seen_ayla_ids | protected_ayla_ids
    deleted, _by_model = owned.exclude(ayla_specialist_service_id__in=keep).delete()
    if deleted:
        logger.info(
            "catalog.reconcile.removed model=MasterService tenant_id=%s count=%d protected=%d",
            tenant.id,
            deleted,
            len(protected_ayla_ids),
        )
    return deleted
