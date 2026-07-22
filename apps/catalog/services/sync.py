"""Catalog sync orchestrator — Ayla internal catalog (S3B / #1044).

Pulls Ayla's canonical catalog and upserts the ``CatalogService`` mirror
under a Redis advisory lock. Called by the Celery beat every 15 minutes
(``apps.catalog.tasks.sync_catalog_for_all_tenants``) and by the admin
"force resync" action.

**S3B STAGE 2 PR-1 scope:** ``CatalogService`` ← ``salon-services`` only.
The tenant-scoped master mirror (``CatalogMaster`` ← ``/internal/specialists/``)
and the bookable ``MasterService`` mirror (← ``specialist-services``) land in
PR-2. ``CatalogFaq`` / ``CatalogHelpArticle`` are retired from the sync cycle
(no Ayla internal source; the mirror tables stay, unsynced).

### Lock semantics — Decision: skip, not queue

Two beats can race when one run is slow. The Redis advisory lock TTL is
deliberately ≥ 1.5× the 15-min beat cadence so a second beat that can't
acquire the lock returns immediately with ``SyncResult.skipped=True``. Skip >
queue because catalog sync is idempotent — the next cycle picks up cleanly.

### No incremental cursor

Ayla's internal catalog exposes no ``?since=`` filter (contract: filters are
``tenant`` / ``template`` / ``is_active``), so each run is a full per-tenant
fetch. The pilot catalog is small; a full fetch every 15 min is cheap.
``Tenant.last_catalog_sync_at`` is still advanced to the newest upstream
``updated_at`` as a freshness signal, but it no longer drives the fetch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.cache import cache

from apps.audit.services import write_audit
from apps.catalog.services.http_client import CatalogHttpClient
from apps.catalog.services.upserter import (
    UpsertResult,
    upsert_salon_services,
    upsert_specialists,
)
from apps.events.services import emit

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Lock key prefix — one slot per tenant. Multi-tenant beat fans out to
# many tenants in parallel; lock per tenant lets one slow tenant not
# block the others.
_LOCK_KEY_PREFIX = "catalog_sync_lock:"

EVENT_CATALOG_SYNCED = "catalog_synced_completed"


@dataclass(frozen=True)
class MirrorCounts:
    """Counter snapshot for one mirror type."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(frozen=True)
class SyncResult:
    """Return value of :meth:`CatalogSyncService.run`.

    Fields:
      ran: True when the lock was acquired and the cycle ran.
      skipped: True when the lock was held by another beat — we returned
               without doing work.
      services: CatalogService (salon-services) mirror counters.
      masters: CatalogMaster (specialists) mirror counters — S3B masters.
      cursor_advanced_to: the new ``last_catalog_sync_at`` value (None when
                          ran=False or no rows touched).
    """

    ran: bool = False
    skipped: bool = False
    services: MirrorCounts = field(default_factory=MirrorCounts)
    masters: MirrorCounts = field(default_factory=MirrorCounts)
    cursor_advanced_to: datetime | None = None
    error: str = ""


class CatalogSyncService:
    """Orchestrate one full sync cycle for one tenant."""

    def __init__(
        self,
        *,
        http_client: Any | None = None,
    ) -> None:
        # ``Any`` instead of CatalogHttpClient — tests inject duck-typed
        # fakes that implement the same fetch_salon_services surface + the
        # context-manager protocol without inheriting the production class.
        self._http = http_client

    def run(self, tenant: "Tenant") -> SyncResult:
        """Run one sync cycle. Returns :class:`SyncResult`.

        Acquires a Redis advisory lock; if held, returns immediately with
        ``skipped=True``. The lock release is best-effort — we ``cache.delete``
        on the way out; if the worker crashes the TTL reclaims the slot.
        """
        lock_key = _LOCK_KEY_PREFIX + str(tenant.id)
        ttl = int(getattr(settings, "CATALOG_SYNC_LOCK_TTL_SECONDS", 25 * 60))

        # `cache.add` is atomic across workers — returns False if the key
        # already exists. Canonical Django distributed advisory lock.
        if not cache.add(lock_key, "1", timeout=ttl):
            logger.info("catalog.sync.skipped reason=lock_held tenant_id=%s", tenant.id)
            return SyncResult(ran=False, skipped=True)

        try:
            return self._run_locked(tenant)
        finally:
            cache.delete(lock_key)

    # ------------------------------------------------------------------
    # Locked path
    # ------------------------------------------------------------------

    def _run_locked(self, tenant: "Tenant") -> SyncResult:
        """Salon-services pull + upsert within the lock window."""
        http = self._http if self._http is not None else CatalogHttpClient()

        try:
            with http:
                salon_dtos = http.fetch_salon_services(tenant_id=str(tenant.id))
        except Exception as exc:  # noqa: BLE001 — orchestrator boundary
            logger.exception("catalog.sync.fetch_failed tenant_id=%s", tenant.id)
            return SyncResult(ran=True, error=str(exc))

        if not salon_dtos:
            # Ayla filters salon-services by the exact ``?tenant=`` UUID. An
            # empty result on a run that succeeded (no error) is either a
            # genuinely empty catalog OR a tenant-id mismatch — the bot
            # ``Tenant.id`` must equal the Ayla Tenant UUID (it does for
            # Ayla-provisioned tenants; ``create_tenant`` mints a local
            # uuid4). Warn so a mis-provisioned pilot tenant can't silently
            # ship an empty mirror that looks identical to a healthy one.
            logger.warning(
                "catalog.sync.empty_fetch tenant_id=%s — Ayla returned 0 "
                "salon-services; if this salon has a catalog, verify Tenant.id "
                "matches the Ayla tenant UUID.",
                tenant.id,
            )

        services_res = upsert_salon_services(tenant, salon_dtos)

        # S3B masters mirror (specialists → CatalogMaster). A specialists
        # fetch failure must not abort the services mirror that already
        # landed — logged, counted as error, services result stands.
        masters_res = UpsertResult()
        try:
            with http:
                specialist_dtos = http.fetch_specialists()
        except Exception as exc:  # noqa: BLE001 — per-mirror isolation
            logger.exception("catalog.sync.fetch_specialists_failed tenant_id=%s", tenant.id)
            specialist_dtos = []
            masters_res.errors.append({"ayla_master_id": "?", "reason": str(exc)})
        else:
            masters_res = upsert_specialists(tenant, specialist_dtos)

        # Freshness signal only — not a fetch cursor (Ayla has no ?since=).
        new_cursor = _max_upstream_ts(salon_dtos)
        if new_cursor is not None:
            tenant.last_catalog_sync_at = new_cursor
            tenant.save(update_fields=["last_catalog_sync_at"])

        result = SyncResult(
            ran=True,
            services=_to_counts(services_res),
            masters=_to_counts(masters_res),
            cursor_advanced_to=new_cursor,
        )

        _audit_and_emit(tenant, result)
        logger.info(
            "catalog.sync.completed tenant_id=%s services=%s/%s/%s masters=%s/%s/%s cursor=%s",
            tenant.id,
            result.services.created,
            result.services.updated,
            result.services.skipped,
            result.masters.created,
            result.masters.updated,
            result.masters.skipped,
            new_cursor.isoformat() if new_cursor else "unchanged",
        )
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_counts(res: UpsertResult) -> MirrorCounts:
    return MirrorCounts(
        created=res.created,
        updated=res.updated,
        skipped=res.skipped,
        errors=len(res.errors),
    )


def _max_upstream_ts(dtos: list[Any]) -> datetime | None:
    """Largest ``external_updated_at`` across DTOs. None if list empty."""
    timestamps = [
        d.external_updated_at for d in dtos if getattr(d, "external_updated_at", None) is not None
    ]
    if not timestamps:
        return None
    return max(timestamps)


def _audit_and_emit(tenant: "Tenant", result: SyncResult) -> None:
    counts_payload = {
        "services": _counts_dict(result.services),
        "masters": _counts_dict(result.masters),
    }
    write_audit(
        EVENT_CATALOG_SYNCED,
        target="Tenant",
        target_id=str(tenant.id),
        payload={
            "tenant_id": str(tenant.id),
            "cursor_advanced_to": (
                result.cursor_advanced_to.isoformat() if result.cursor_advanced_to else None
            ),
            "counts": counts_payload,
        },
    )
    emit(
        EVENT_CATALOG_SYNCED,
        distinct_id=str(tenant.id),
        properties={"counts": counts_payload},
    )


def _counts_dict(c: MirrorCounts) -> dict[str, int]:
    return {
        "created": c.created,
        "updated": c.updated,
        "skipped": c.skipped,
        "errors": c.errors,
    }


__all__ = [
    "CatalogSyncService",
    "EVENT_CATALOG_SYNCED",
    "MirrorCounts",
    "SyncResult",
]


# Suppress unused-import linter on datetime/timezone — used only via type
# annotations on the dataclass fields.
_ = (datetime, timezone)
