"""Catalog-mirror upserter (DRF-574 / Sprint 7 / C3).

Idempotent INSERT-or-UPDATE for the four
:mod:`apps.catalog.models` mirrors, driven by DTOs the C2 HTTP client
produces. Used by:

* C4 (DRF-575) — the periodic sync orchestrator
* C6 (DRF-576) — admin "force resync" action

### Conflict resolution — Decision 10

When two beats race (slow first run still in flight; second beat starts;
both upsert the same external_id), the **upstream timestamp** decides
who wins, NOT wall-clock-of-write. The upserter only OVERWRITES a row
when ``incoming.external_updated_at > existing.external_updated_at``.
A stale DTO (older upstream timestamp) is silently skipped — this
implements last-writer-wins by upstream-source-of-truth, which is what
Risk #5 in the plan flagged.

### Per-row error isolation

A single malformed DTO must NOT abort the rest of the batch — the
sync orchestrator runs every 15 minutes and should make forward progress
on every other row. Failures land in :class:`UpsertResult.errors` with
the external_id + reason; the orchestrator logs them and continues.

### Transaction semantics

Each ``upsert_*`` call wraps its loop in a single transaction so the
result-counter snapshot is consistent. A row failure rolls back ONLY
that row (savepoints) — pre-existing rows committed by earlier
iterations stay committed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.db import transaction

from apps.catalog.models import (
    CatalogFaq,
    CatalogHelpArticle,
    CatalogMaster,
    CatalogService,
)

if TYPE_CHECKING:
    from datetime import datetime

    from apps.catalog.services.http_client import (
        CatalogFaqDTO,
        CatalogHelpArticleDTO,
        CatalogMasterDTO,
        CatalogServiceDTO,
    )
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@dataclass
class UpsertResult:
    """Counter snapshot a single upsert_* call writes.

    Fields:
      created: rows whose ``(tenant, external_id)`` wasn't present before.
      updated: rows present + incoming external_updated_at is newer.
      skipped: rows present + incoming external_updated_at is stale.
      errors: per-row failures with ``{external_id, reason}`` so the
              caller can blame the right input on a partial batch.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API — one method per mirror
# ---------------------------------------------------------------------------


def upsert_services(tenant: "Tenant", dtos: list["CatalogServiceDTO"]) -> UpsertResult:
    return _upsert_batch(
        tenant=tenant,
        dtos=dtos,
        model=CatalogService,
        field_mapper=_service_fields,
    )


def upsert_masters(tenant: "Tenant", dtos: list["CatalogMasterDTO"]) -> UpsertResult:
    return _upsert_batch(
        tenant=tenant,
        dtos=dtos,
        model=CatalogMaster,
        field_mapper=_master_fields,
    )


def upsert_faqs(tenant: "Tenant", dtos: list["CatalogFaqDTO"]) -> UpsertResult:
    return _upsert_batch(
        tenant=tenant,
        dtos=dtos,
        model=CatalogFaq,
        field_mapper=_faq_fields,
    )


def upsert_help_articles(tenant: "Tenant", dtos: list["CatalogHelpArticleDTO"]) -> UpsertResult:
    return _upsert_batch(
        tenant=tenant,
        dtos=dtos,
        model=CatalogHelpArticle,
        field_mapper=_help_article_fields,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _upsert_batch(
    *,
    tenant: "Tenant",
    dtos: list[Any],
    model: Any,
    field_mapper: Any,
) -> UpsertResult:
    """Shared loop. Per-row errors don't abort the batch — savepoint
    rollback isolates failures.
    """
    result = UpsertResult()
    with transaction.atomic():
        for dto in dtos:
            try:
                _upsert_one(
                    tenant=tenant,
                    dto=dto,
                    model=model,
                    field_mapper=field_mapper,
                    result=result,
                )
            except Exception as exc:  # noqa: BLE001 — per-row safety net
                # Each row is a savepoint thanks to the inner atomic
                # block in :func:`_upsert_one`; the outer transaction
                # absorbs the rollback. Log + record + carry on.
                external_id = getattr(dto, "external_id", "?")
                result.errors.append({"external_id": external_id, "reason": str(exc)})
                logger.exception(
                    "catalog.upsert.row_failed model=%s external_id=%s",
                    model.__name__,
                    external_id,
                )
    return result


def _upsert_one(
    *,
    tenant: "Tenant",
    dto: Any,
    model: Any,
    field_mapper: Any,
    result: UpsertResult,
) -> None:
    """One row. Wrapped in a savepoint so failures don't poison the batch."""
    with transaction.atomic():
        existing = (
            model.all_tenants.filter(
                tenant=tenant,
                external_id=dto.external_id,
            )
            .only("id", "external_updated_at")
            .first()
        )
        # Last-writer-wins on upstream timestamp.
        if existing is not None and not _incoming_is_newer(
            existing.external_updated_at, dto.external_updated_at
        ):
            result.skipped += 1
            return

        fields = field_mapper(dto)
        if existing is None:
            model.all_tenants.create(
                tenant=tenant,
                external_id=dto.external_id,
                external_updated_at=dto.external_updated_at,
                **fields,
            )
            result.created += 1
        else:
            # update_or_create-style; we already located the row and
            # know it's newer.
            model.all_tenants.filter(pk=existing.pk).update(
                external_updated_at=dto.external_updated_at,
                **fields,
            )
            result.updated += 1


def _incoming_is_newer(existing: "datetime", incoming: "datetime") -> bool:
    """True when ``incoming`` is strictly newer.

    Equal timestamps are treated as "no change" → skip. This matters
    when two beats race against an unchanged row.
    """
    return incoming > existing


# ---------------------------------------------------------------------------
# Per-model field mappers
# ---------------------------------------------------------------------------


def _service_fields(dto: "CatalogServiceDTO") -> dict[str, Any]:
    return {
        "slug": dto.slug,
        "name": dto.name,
        "short_description": dto.short_description,
        "description": dto.description,
        "price_from": dto.price_from,
        "duration_min": dto.duration_min,
        "is_active": dto.is_active,
        "is_popular": dto.is_popular,
        "seo_title": dto.seo_title,
        "seo_description": dto.seo_description,
        "goals": dto.goals,
        "requires_health_check": dto.requires_health_check,
        "contraindications": dto.contraindications,
        "raw": dto.raw,
    }


def _master_fields(dto: "CatalogMasterDTO") -> dict[str, Any]:
    return {
        "name": dto.name,
        "specialization": dto.specialization,
        "bio": dto.bio,
        "experience": dto.experience,
        "rating": dto.rating,
        "is_active": dto.is_active,
        "yclients_staff_id": dto.yclients_staff_id,
        "raw": dto.raw,
    }


def _faq_fields(dto: "CatalogFaqDTO") -> dict[str, Any]:
    return {
        "question": dto.question,
        "answer": dto.answer,
        "category_slug": dto.category_slug,
        "raw": dto.raw,
    }


def _help_article_fields(dto: "CatalogHelpArticleDTO") -> dict[str, Any]:
    return {
        "question": dto.question,
        "answer": dto.answer,
        "order": dto.order,
        "is_active": dto.is_active,
        "raw": dto.raw,
    }
