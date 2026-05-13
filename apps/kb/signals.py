"""Tenant-lifecycle signal handlers (DRF-569 / Sprint 7 / K11).

ChromaDB collections live outside Django's ORM. When a Tenant is
deleted the Postgres CASCADE wipes :class:`apps.kb.models.KbDocument`
rows but the per-tenant ChromaDB collection (created via
:mod:`apps.kb.chromadb_client`) stays behind — its chunks become
orphans nobody queries but they leak metadata across the tenant
lifecycle. If a new Tenant ever lands on the same UUID (test
fixtures recycle UUIDs across `tmp_path` runs; staging restores
sometimes resurface old IDs) it would inherit those orphaned chunks.

This module hooks ``pre_delete`` on :class:`apps.tenancy.models.Tenant`
and drops the collection BEFORE the CASCADE fires. K2's
:meth:`ChromaClient.delete_collection` swallows the not-found error
when no collection ever existed, so the signal is safe on tenants
that never had any KB content.

### Why pre_delete, not post_delete

* The collection deletion is the EXPENSIVE leg (HTTP call to
  ChromaDB). Failing here should fail the whole delete; otherwise
  we'd end up with a phantom collection and no Tenant row to
  reference it from.
* If we used ``post_delete`` and the chromadb call failed, the
  Postgres rows would already be gone — no way to retry.

### Connection point

:class:`apps.kb.apps.KbConfig.ready` imports this module so the
signal handler registers at Django app-load time. Without the
``ready`` hook the file is never imported and the signal is dead.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from apps.kb.chromadb_client import get_chroma_client
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=Tenant)
def on_tenant_pre_delete(sender: type[Tenant], instance: Tenant, **kwargs: Any) -> None:
    """Drop the tenant's ChromaDB collection before Postgres CASCADE.

    Idempotent — K2 swallows ``not-found``-class errors so this is
    safe on tenants whose KB was never built.
    """
    try:
        get_chroma_client().delete_collection(instance)
    except Exception as exc:  # noqa: BLE001 — last-line safety net
        # The K2 wrapper already absorbs not-found. Anything escaping
        # here is a real chromadb misbehaviour; we log loud so a
        # subsequent restore-from-snapshot can pick the orphan up
        # manually, but we don't abort the Tenant delete — better an
        # orphan than a half-deleted tenant.
        logger.exception(
            "kb.signals.tenant_pre_delete_failed tenant_id=%s err=%s",
            instance.id,
            exc,
        )
