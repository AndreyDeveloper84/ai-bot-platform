"""apply_delta Celery task — fetch + upsert/delete one mirror row.

Sprint 10 / C3 (DRF-879). Driven by the HMAC-verified webhook
(``apps.catalog.webhooks.views``). Decoupled from the request path so
the receiver returns 200 fast even when mysite is slow.

## Idempotency

The same ``(model, external_id, external_updated_at)`` triple replayed
twice produces the same end state — Sprint 7's upserter (DRF-574)
already handles that via ``(tenant, external_id)`` unique. We add a
**stale-update guard**: if the row's local ``external_updated_at`` is
newer than the incoming one, skip the upsert (out-of-order delivery
shouldn't roll state back).

## Failure handling

* Tenant not found → audit ``apply_delta.tenant_missing``, no retry
* mysite fetch fails (404/5xx) → audit + Celery retry per task policy
* Upsert fails (DB constraint, etc.) → audit + raise (Celery retry +
  page on permanent failure)

## What's NOT done here

The push receiver covers fine-grained updates. The full-table refresh
(15-min Celery beat from Sprint 7 / DRF-575) stays — push is best-effort
for latency, pull is the eventual-consistency guarantee.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]
from django.utils.dateparse import parse_datetime

from apps.audit.services import write_audit
from apps.catalog.models import (
    CatalogFaq,
    CatalogHelpArticle,
    CatalogMaster,
    CatalogService,
)
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


_MODEL_MAP: dict[str, type[Any]] = {
    "service": CatalogService,
    "master": CatalogMaster,
    "faq": CatalogFaq,
    "help_article": CatalogHelpArticle,
}


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    retry_backoff=True,
    retry_backoff_max=300,
)
def apply_delta(
    self: Any,
    *,
    tenant_slug: str,
    model: str,
    event: str,
    external_id: int,
    external_updated_at: str,
) -> dict[str, Any]:
    """Apply a single delta from the mysite webhook.

    Returns a status dict for telemetry/test visibility:

    ``{"status": "applied" | "deleted" | "skipped_stale" | "tenant_missing" | "model_missing", ...}``

    Never raises for business-class failures — only for transient
    infra failures (DB unreachable etc.) where Celery retry helps.
    """
    model_cls = _MODEL_MAP.get(model)
    if model_cls is None:
        # Belt-and-braces — view layer filters this. If it reaches here
        # it's a bug, not a delivery issue.
        logger.warning("apply_delta.unknown_model model=%s", model)
        write_audit(
            action="catalog.apply_delta.failed",
            target=f"catalog.{model}",
            payload={"reason": "unknown_model", "external_id": external_id},
        )
        return {"status": "model_missing", "model": model}

    try:
        tenant = Tenant.objects.get(slug=tenant_slug)
    except Tenant.DoesNotExist:
        # mysite sent a slug we don't know — could be a typo, could be
        # an out-of-sync deploy. Audit and stop; the retry doesn't help.
        logger.warning("apply_delta.tenant_missing slug=%s", tenant_slug)
        write_audit(
            action="catalog.apply_delta.failed",
            target=f"catalog.{model}",
            payload={
                "reason": "tenant_missing",
                "tenant_slug": tenant_slug,
                "external_id": external_id,
            },
        )
        return {"status": "tenant_missing", "tenant_slug": tenant_slug}

    if event == "deleted":
        return _handle_delete(tenant, model_cls, model, external_id)

    incoming_ts = parse_datetime(external_updated_at)
    if incoming_ts is None:
        logger.warning(
            "apply_delta.bad_timestamp tenant=%s model=%s external_id=%d ts=%r",
            tenant_slug,
            model,
            external_id,
            external_updated_at,
        )
        write_audit(
            action="catalog.apply_delta.failed",
            target=f"catalog.{model}",
            payload={
                "reason": "bad_timestamp",
                "tenant_slug": tenant_slug,
                "external_id": external_id,
                "received": external_updated_at,
            },
        )
        return {"status": "bad_timestamp", "external_id": external_id}

    return _handle_upsert(
        tenant=tenant,
        model_cls=model_cls,
        model=model,
        external_id=external_id,
        external_updated_at=incoming_ts,
    )


def _handle_delete(
    tenant: Any, model_cls: type[Any], model: str, external_id: int
) -> dict[str, Any]:
    """Soft-evict: delete the mirror row. mysite is canonical — when it
    says delete, we don't keep stale state."""
    deleted, _ = model_cls.all_tenants.filter(tenant=tenant, external_id=external_id).delete()
    write_audit(
        action="catalog.apply_delta.deleted",
        target=f"catalog.{model}",
        payload={
            "tenant_slug": tenant.slug,
            "external_id": external_id,
            "rows_removed": deleted,
        },
    )
    return {"status": "deleted", "rows_removed": deleted}


def _handle_upsert(
    *,
    tenant: Any,
    model_cls: type[Any],
    model: str,
    external_id: int,
    external_updated_at: datetime,
) -> dict[str, Any]:
    """Stale-update guard + bump-only upsert.

    Phase 1 wire-up note: this writes only ``external_updated_at`` —
    the webhook is a "something changed" signal, not a full payload.
    The 15-min beat (Sprint 7 / DRF-575) refreshes the rest of the row
    on its next pass. A new row created here starts with default
    string/int fields; the beat overwrites them.
    """
    existing = model_cls.all_tenants.filter(tenant=tenant, external_id=external_id).first()
    if existing is not None and existing.external_updated_at >= external_updated_at:
        write_audit(
            action="catalog.apply_delta.skipped_stale",
            target=f"catalog.{model}",
            payload={
                "tenant_slug": tenant.slug,
                "external_id": external_id,
                "incoming_updated_at": external_updated_at.isoformat(),
                "local_updated_at": existing.external_updated_at.isoformat(),
            },
        )
        return {"status": "skipped_stale", "external_id": external_id}

    _obj, created = model_cls.all_tenants.update_or_create(
        tenant=tenant,
        external_id=external_id,
        defaults={"external_updated_at": external_updated_at},
    )
    write_audit(
        action="catalog.apply_delta.applied",
        target=f"catalog.{model}",
        payload={
            "tenant_slug": tenant.slug,
            "external_id": external_id,
            "created": created,
            "external_updated_at": external_updated_at.isoformat(),
        },
    )
    return {
        "status": "applied",
        "external_id": external_id,
        "created": created,
    }
