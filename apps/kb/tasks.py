"""KB reindex Celery tasks (DRF-564 / Sprint 7 / K6).

Two entrypoints:

* :func:`reindex_tenant_kb` — admin "force resync" surface (K9 button).
  Walks every :class:`apps.kb.models.KbDocument` for a tenant +
  optional ``doc_type`` filter, calls K4 :func:`ingest_document` per
  row. Force=True so the checksum-skip guard is bypassed.
* :func:`embed_pending_kb_documents` — periodic catch-up sweep.
  Picks rows where ``embedded_at IS NULL OR embedded_at < updated_at``
  (the "needs embedding" predicate) and ingests them with default
  skip semantics.

### Per-doc isolation

Each document gets its own try/except. A single embedding failure (an
LLM 429, malformed input, whatever) is logged, counted, and the loop
continues. The whole batch never aborts because of one row. This
matters: the prior nightly sweep that touched 800 KbDocuments
shouldn't roll back its progress because doc 801 had a wrong-type
payload.

### Provider injection

The task constructs an :class:`apps.llm.providers.openai_provider.OpenAIProvider`
by default. Tests pass a fake provider via a kwarg. Sprint 7 / L5
router (DRF-587) lands a per-tenant router; once it does, K6 will
read from it instead of hard-wiring OpenAI.

### Batching

We yield to other Celery tasks after every 100 documents by reaping
the iterator in chunks. For Sprint 7's single-tenant footprint the
queue depth is low, but the pattern is here so Sprint 8 multi-tenant
doesn't have to refactor it.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import shared_task  # type: ignore[import-untyped]
from django.db.models import Q

from apps.kb.models import KbDocument
from apps.kb.services.ingester import ingest_document
from apps.llm.protocol import LLMError, LLMProvider
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Tunable per-batch yield. Picked to keep one ingest cycle under
# Sprint 1's CELERY_TASK_SOFT_TIME_LIMIT (30 min) at p95 latency.
_YIELD_EVERY = 100


@shared_task(name="apps.kb.tasks.reindex_tenant_kb")
def reindex_tenant_kb(
    tenant_id: str,
    *,
    doc_type: str | None = None,
    force: bool = True,
) -> dict[str, int]:
    """Force-resync all KB docs for ``tenant_id``.

    Args:
      tenant_id: UUID of the Tenant to resync (str — Celery serialises
                 UUIDs to str on enqueue).
      doc_type: optional restriction to one :class:`KbDocType` value.
                Useful when only the FAQ table changed upstream.
      force: bypass the checksum-skip guard. Default True for the
             admin "force resync" path — operators usually click this
             precisely because the skip heuristic missed something.

    Returns:
      ``{"processed": N, "ingested": N, "skipped": N, "failed": N}`` —
      surfaced to the admin action's confirmation message.
    """
    # Tenant doesn't use the TenantScopedManager (it IS the tenant
    # registry); the default manager filters `is_active=True`, so
    # use the `all_objects` registry to also resync deactivated tenants
    # when an operator triggers a force-resync from admin.
    tenant = Tenant.objects.get(id=UUID(str(tenant_id)))
    queryset = KbDocument.all_tenants.filter(tenant=tenant)
    if doc_type:
        queryset = queryset.filter(doc_type=doc_type)
    return _ingest_queryset(queryset, force=force, label="reindex")


@shared_task(name="apps.kb.tasks.sync_catalog_to_kb")
def sync_catalog_to_kb(tenant_id: str) -> dict[str, int]:
    """Project catalog mirrors → KbDocument rows, then trigger ingest.

    Sprint 7 / K7 (DRF-565). Two-phase:

    1. **Project** — :func:`apps.kb.projectors.project_tenant_catalog`
       walks each mirror table, upserts a matching KbDocument row,
       and reports created / updated / unchanged counts.
    2. **Re-embed** — projector touched only changed-content rows
       (via the checksum diff). The follow-up
       :func:`embed_pending_kb_documents` call picks those rows
       (their ``updated_at`` advanced past ``embedded_at``); unchanged
       rows skip on K4's checksum guard with no OpenAI spend.

    Called from the C4 sync_catalog_for_all_tenants beat success
    handler — chained via ``.delay(tenant_id)`` once a SyncResult
    with ``ran=True`` lands. Sprint 7 wires the chain manually; Sprint
    8 may flip to a Celery ``chord`` for atomic projection+ingest.

    Returns merged counter dict: per-doc-type projection counts +
    overall ingest counters.
    """
    from apps.kb.projectors import project_tenant_catalog

    tenant = Tenant.objects.get(id=UUID(str(tenant_id)))
    projection = project_tenant_catalog(tenant)

    # Re-embed only the changed-content rows. The simpler path is to
    # invoke embed_pending_kb_documents for the tenant — it already
    # selects rows where embedded_at < updated_at, which matches the
    # set the projector just touched (its UPDATE bumps updated_at via
    # Django auto_now). Skip path covers unchanged rows for free.
    embed_counters = embed_pending_kb_documents(tenant_id=str(tenant.id))

    out: dict[str, int] = {f"embed_{k}": v for k, v in embed_counters.items()}
    for doc_type, result in projection.items():
        out[f"{doc_type}_created"] = result.created
        out[f"{doc_type}_updated"] = result.updated
        out[f"{doc_type}_unchanged"] = result.unchanged
    return out


@shared_task(name="apps.kb.tasks.embed_pending_kb_documents")
def embed_pending_kb_documents(*, tenant_id: str | None = None) -> dict[str, int]:
    """Embed every row whose checksum has drifted since last ingest.

    Selector: ``embedded_at IS NULL OR embedded_at < updated_at``.
    Optional ``tenant_id`` narrows to one tenant — Sprint 8 multi-tenant
    beat fans this out per tenant. Sprint 7 ships single-tenant so the
    no-tenant call walks the whole catalog.

    Returns the same counter shape as :func:`reindex_tenant_kb`.
    """
    queryset = KbDocument.all_tenants.filter(
        Q(embedded_at__isnull=True) | Q(embedded_at__lt=models_lookup_self_updated_at())
    )
    if tenant_id is not None:
        queryset = queryset.filter(tenant_id=UUID(str(tenant_id)))
    return _ingest_queryset(queryset, force=False, label="pending_sweep")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ingest_queryset(
    queryset: Any,
    *,
    force: bool,
    label: str,
    provider: LLMProvider | None = None,
) -> dict[str, int]:
    """Walk ``queryset`` with per-doc isolation. ``label`` ends up in logs."""
    llm = provider if provider is not None else _build_default_provider()
    counters = {"processed": 0, "ingested": 0, "skipped": 0, "failed": 0}

    # `iterator(chunk_size=...)` streams rows without buffering the
    # whole queryset in memory — important when the K8 legacy migration
    # walks ten-thousand rows.
    for index, doc in enumerate(queryset.iterator(chunk_size=_YIELD_EVERY)):
        counters["processed"] += 1
        try:
            result = ingest_document(doc, provider=llm, force=force)
            if result.ingested:
                counters["ingested"] += 1
            else:
                counters["skipped"] += 1
        except LLMError:
            counters["failed"] += 1
            logger.exception(
                "kb.task.%s_failed doc_id=%s tenant_id=%s",
                label,
                doc.id,
                doc.tenant_id,
            )
        # Yield-friendly: log progress every _YIELD_EVERY rows so a long
        # sweep is observable. Celery's prefetch_multiplier=1 (Sprint 1)
        # means we don't need explicit yields for queue fairness.
        if (index + 1) % _YIELD_EVERY == 0:
            logger.info(
                "kb.task.%s_progress processed=%s ingested=%s skipped=%s failed=%s",
                label,
                counters["processed"],
                counters["ingested"],
                counters["skipped"],
                counters["failed"],
            )

    logger.info(
        "kb.task.%s_completed processed=%s ingested=%s skipped=%s failed=%s",
        label,
        counters["processed"],
        counters["ingested"],
        counters["skipped"],
        counters["failed"],
    )
    return counters


def _build_default_provider() -> LLMProvider:
    """Construct the default OpenAI provider. Tests override via
    ``_ingest_queryset(provider=...)`` so this is unused in unit-test paths.
    """
    from apps.llm.providers.openai_provider import OpenAIProvider

    return OpenAIProvider()


def models_lookup_self_updated_at() -> Any:
    """Django ORM expression for "the updated_at field of THIS row".

    Used in the Q() filter ``embedded_at < updated_at`` — Django doesn't
    let you compare two columns of the same model with a literal
    ``__lt=F('updated_at')`` chain on a Q, so we wrap it here for
    readability and so tests can monkeypatch easily.
    """
    from django.db.models import F

    return F("updated_at")
