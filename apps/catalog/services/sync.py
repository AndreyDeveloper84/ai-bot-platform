"""Catalog sync orchestrator (DRF-575 / Sprint 7 / C4).

Walks C2 fetchers → C3 upserter for all four mirror types under a
Redis advisory lock. Called by the Celery beat (C5 / DRF-579) every
15 minutes and by the admin "force resync" action (C6 / DRF-576).

### Lock semantics — Decision: skip, not queue

Two beats can race when one run is slow (network blip on mysite,
upserter chewing a large batch). The Redis advisory lock TTL is
deliberately ≥ 1.5× the 15-min beat cadence so:

* First beat acquires the lock, runs for ≤ 25 min.
* Second beat tries to acquire → fails → **returns immediately** with
  ``SyncResult.skipped=True``.

Skip > queue because catalog sync is **idempotent** — the next cycle
picks up where we left off. Queuing would just stack-up work the
running beat will redo anyway when its cursor advances past those
rows.

### Cursor semantics

The cursor (``Tenant.last_catalog_sync_at``) is the upstream timestamp
of the most recent row we successfully upserted. mysite's
``?since=`` filter is strict greater-than, so the next run sees only
rows mysite has touched after the cursor. Forward-only — no row gets
fetched twice across runs.

NULL cursor = full resync (initial bootstrap, or admin force-clear).

### Atomic cursor advance

Cursor advances ONLY after every mirror's upsert completed without
raising. If one mirror's HTTP fetch raises, the cursor stays at the
previous value — the next run retries the whole bundle. We don't
advance per-mirror because the four mirrors are conceptually one
catalog snapshot.
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
    upsert_faqs,
    upsert_help_articles,
    upsert_masters,
    upsert_services,
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
      skipped: True when the lock was held by another beat — we
               returned without doing work.
      services / masters / faqs / help_articles: per-mirror counters.
      cursor_advanced_to: the new ``last_catalog_sync_at`` value
                          (None when ran=False or no rows touched).
    """

    ran: bool = False
    skipped: bool = False
    services: MirrorCounts = field(default_factory=MirrorCounts)
    masters: MirrorCounts = field(default_factory=MirrorCounts)
    faqs: MirrorCounts = field(default_factory=MirrorCounts)
    help_articles: MirrorCounts = field(default_factory=MirrorCounts)
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
        # fakes that implement the same fetch_* surface without
        # inheriting the production class. The four fetch methods used
        # below pin the implicit Protocol.
        self._http = http_client

    def run(self, tenant: "Tenant") -> SyncResult:
        """Run one sync cycle. Returns :class:`SyncResult`.

        Acquires a Redis advisory lock; if held, returns immediately
        with ``skipped=True``. The lock release is best-effort — we
        ``cache.delete`` on the way out; if the worker crashes the
        TTL (25 min) reclaims the slot.
        """
        lock_key = _LOCK_KEY_PREFIX + str(tenant.id)
        ttl = int(getattr(settings, "CATALOG_SYNC_LOCK_TTL_SECONDS", 25 * 60))

        # `cache.add` is atomic across workers — returns False if the
        # key already exists. This is the canonical Django way to get
        # a distributed advisory lock without an extra Redis lib.
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
        """All mirror pulls + upserts within the lock window."""
        http = self._http if self._http is not None else CatalogHttpClient()
        since = tenant.last_catalog_sync_at

        try:
            with http:
                services_dtos = http.fetch_services(since=since)
                masters_dtos = http.fetch_masters(since=since)
                faqs_dtos = http.fetch_faqs(since=since)
                help_dtos = http.fetch_help_articles(since=since)
        except Exception as exc:  # noqa: BLE001 — orchestrator boundary
            logger.exception("catalog.sync.fetch_failed tenant_id=%s", tenant.id)
            return SyncResult(ran=True, error=str(exc))

        services_res = upsert_services(tenant, services_dtos)
        masters_res = upsert_masters(tenant, masters_dtos)
        faqs_res = upsert_faqs(tenant, faqs_dtos)
        help_res = upsert_help_articles(tenant, help_dtos)

        # Cursor = the max upstream timestamp across what we pulled.
        # We could write per-mirror cursors but the catalog is a single
        # logical snapshot, so one cursor keeps state simple.
        new_cursor = _max_upstream_ts(
            [
                *services_dtos,
                *masters_dtos,
                *faqs_dtos,
                *help_dtos,
            ]
        )
        if new_cursor is not None:
            tenant.last_catalog_sync_at = new_cursor
            tenant.save(update_fields=["last_catalog_sync_at"])

        result = SyncResult(
            ran=True,
            services=_to_counts(services_res),
            masters=_to_counts(masters_res),
            faqs=_to_counts(faqs_res),
            help_articles=_to_counts(help_res),
            cursor_advanced_to=new_cursor,
        )

        _audit_and_emit(tenant, result)
        logger.info(
            "catalog.sync.completed tenant_id=%s services=%s/%s/%s masters=%s/%s/%s "
            "faqs=%s/%s/%s help=%s/%s/%s cursor=%s",
            tenant.id,
            result.services.created,
            result.services.updated,
            result.services.skipped,
            result.masters.created,
            result.masters.updated,
            result.masters.skipped,
            result.faqs.created,
            result.faqs.updated,
            result.faqs.skipped,
            result.help_articles.created,
            result.help_articles.updated,
            result.help_articles.skipped,
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
        "faqs": _counts_dict(result.faqs),
        "help_articles": _counts_dict(result.help_articles),
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


# Suppress unused-import linter on datetime/timezone — they're used
# only via type annotations on the dataclass fields.
_ = (datetime, timezone)
