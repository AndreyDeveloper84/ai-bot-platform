"""Catalog mirror → KbDocument projectors (DRF-565 / Sprint 7 / K7).

Each catalog mirror (Service, Master, FAQ, HelpArticle) projects into
a :class:`apps.kb.models.KbDocument` row with the right ``doc_type``
slug + textual body. The :func:`sync_catalog_to_kb` Celery task wires
all four projectors and chains after C4 catalog sync completion.

### Why "projector" not "transformer"

A projector is intentionally **lossy** — we throw away mirror fields
that don't help retrieval (price, raw JSON dump, etc.). The KB body
is what the embedder sees + what surfaces as a retrieved chunk; it
should read like prose, not a database dump. Sprint 8 may add a
"retrieval format" registry per doc_type if the format diverges
across tenants; Sprint 7 keeps the projectors as pure functions.

### Source URI contract

The KB ``source_uri`` couples mirror rows back to the original mysite
PK via ``mysite://<resource>/<external_id>``. Same scheme the catalog
HTTP client uses for traceability.

### Idempotency

A second sync_catalog_to_kb run with no mirror changes is a no-op:

* :func:`build_kb_document` ``update_or_create`` matches on
  ``(tenant, source_uri, version)``. Sprint 7 uses ``version=1`` for
  every mirror row (versioning lives on the mirror, not here); Sprint 8
  may evolve the version key for content-snapshot history.
* :func:`apps.kb.services.ingester.ingest_document` skips when the
  checksum hasn't moved — embedded_at gets bumped without burning
  OpenAI calls.

So the steady-state behaviour after the first sync is: project →
update_or_create (no diff → no write) → ingest (skip) → embedded_at
re-stamped. ~1 query per row per tick, no embedding spend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.kb.models import KbDocType, KbDocument

if TYPE_CHECKING:
    from apps.catalog.models import (
        CatalogFaq,
        CatalogHelpArticle,
        CatalogMaster,
        CatalogService,
    )
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectionResult:
    """Counter snapshot for a single mirror→KB projection pass."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0


# ---------------------------------------------------------------------------
# Body builders — pure functions per mirror type
# ---------------------------------------------------------------------------


def _goal_labels(goals: object) -> list[str]:
    """`CatalogService.goals` → human labels for the retrieval body.

    Two shapes are accepted on purpose. Since DRF-1308 sync writes
    ``{"key", "label"}`` objects — the key is what a structured goal filter
    needs, the label is what belongs in text a model quotes back to a
    person ("Подходит: relax" would be leaked jargon). Bare strings are
    still honoured because rows synced before that change may sit in the
    mirror until the next 15-minute pass rewrites them.
    """
    if not isinstance(goals, list):
        return []
    labels: list[str] = []
    for entry in goals:
        if isinstance(entry, str) and entry:
            labels.append(entry)
        elif isinstance(entry, dict):
            label = entry.get("label") or entry.get("key")
            if isinstance(label, str) and label:
                labels.append(label)
    return labels


def service_to_body(row: "CatalogService") -> str:
    """Service mirror → KB body.

    Keeps name + descriptions + price + duration + goals + contraindications.
    Discards raw, seo_*, is_popular, slug (those are filterable on the
    mirror; embedding them as text adds noise).
    """
    parts: list[str] = [f"Услуга: {row.name}"]
    if row.short_description:
        parts.append(row.short_description)
    if row.description:
        parts.append(row.description)
    if row.price_from is not None:
        parts.append(f"Цена от: {row.price_from}")
    if row.duration_min is not None:
        parts.append(f"Длительность: {row.duration_min} мин")
    goal_labels = _goal_labels(row.goals)
    if goal_labels:
        parts.append("Подходит: " + ", ".join(goal_labels))
    if row.requires_health_check:
        parts.append("Требует медконсультации.")
    if row.contraindications:
        parts.append(f"Противопоказания: {row.contraindications}")
    return "\n".join(parts)


def master_to_body(row: "CatalogMaster") -> str:
    parts: list[str] = [f"Мастер: {row.name}"]
    if row.specialization:
        parts.append(f"Специализация: {row.specialization}")
    if row.bio:
        parts.append(row.bio)
    if row.experience:
        parts.append(f"Опыт: {row.experience}")
    # Domain is 1..5 — a stored 0.00 means «no reviews behind it», not
    # «rated zero» (DRF-1224). This body is retrieval text the model quotes
    # back to the user, so projecting it is the discovery-card leak one hop
    # later.
    if row.rating is not None and row.rating >= 1:
        parts.append(f"Рейтинг: {row.rating}")
    return "\n".join(parts)


def faq_to_body(row: "CatalogFaq") -> str:
    return f"Q: {row.question}\nA: {row.answer}"


def help_article_to_body(row: "CatalogHelpArticle") -> str:
    return f"{row.question}\n{row.answer}"


# ---------------------------------------------------------------------------
# Projection orchestrator
# ---------------------------------------------------------------------------


_PROJECTORS = (
    (
        KbDocType.SERVICE,
        lambda row: f"mysite://services/{row.external_id}",
        service_to_body,
    ),
    (
        KbDocType.MASTER,
        lambda row: f"mysite://masters/{row.external_id}",
        master_to_body,
    ),
    (
        KbDocType.FAQ,
        lambda row: f"mysite://faqs/{row.external_id}",
        faq_to_body,
    ),
    (
        KbDocType.HELP_ARTICLE,
        lambda row: f"mysite://help-articles/{row.external_id}",
        help_article_to_body,
    ),
)


def project_tenant_catalog(tenant: "Tenant") -> dict[str, ProjectionResult]:
    """Project all four catalog mirrors into :class:`KbDocument` rows.

    Returns a per-doc-type result dict — the K7 Celery task surface
    layers this into its overall counters.

    Order: services → masters → faqs → help articles. Each mirror runs
    independently; a single row's IntegrityError or unexpected exception
    bubbles up — we don't try to half-project (the K7 task's outer
    try/except catches per-tenant).
    """
    from apps.catalog.models import (
        CatalogFaq,
        CatalogHelpArticle,
        CatalogMaster,
        CatalogService,
    )

    results: dict[str, ProjectionResult] = {}

    pairings = (
        (KbDocType.SERVICE, CatalogService),
        (KbDocType.MASTER, CatalogMaster),
        (KbDocType.FAQ, CatalogFaq),
        (KbDocType.HELP_ARTICLE, CatalogHelpArticle),
    )

    for doc_type, model in pairings:
        results[doc_type] = _project_one_mirror(tenant, doc_type=doc_type, model=model)
    return results


def _project_one_mirror(
    tenant: "Tenant",
    *,
    doc_type: str,
    model: Any,
) -> ProjectionResult:
    """Walk one mirror table; create-or-update :class:`KbDocument` rows."""
    # Pick the matching body builder + URI scheme.
    uri_fn, body_fn = _projector_for(doc_type)

    created = 0
    updated = 0
    unchanged = 0
    for row in model.all_tenants.filter(tenant=tenant).iterator():
        source_uri = uri_fn(row)
        new_body = body_fn(row)
        new_checksum = KbDocument.compute_checksum(new_body)

        existing = (
            KbDocument.all_tenants.filter(
                tenant=tenant,
                source_uri=source_uri,
                version=1,
            )
            .only("id", "checksum")
            .first()
        )
        if existing is None:
            KbDocument.all_tenants.create(
                tenant=tenant,
                doc_type=doc_type,
                source_uri=source_uri,
                version=1,
                content=new_body,
                checksum=new_checksum,
                metadata={"projected_from": model.__name__},
            )
            created += 1
            continue

        if existing.checksum == new_checksum:
            unchanged += 1
            continue

        # Content drifted — update without bumping version. Version
        # bumps are reserved for explicit "I want both rows queryable"
        # forensic flows.
        KbDocument.all_tenants.filter(pk=existing.pk).update(
            content=new_body,
            checksum=new_checksum,
            # `embedded_at` left as-is — K4 ingester detects the
            # checksum drift on its next pass and re-embeds.
        )
        updated += 1

    return ProjectionResult(created=created, updated=updated, unchanged=unchanged)


def _projector_for(doc_type: str):
    """Return ``(uri_fn, body_fn)`` for the given doc_type."""
    for dt, uri_fn, body_fn in _PROJECTORS:
        if dt == doc_type:
            return (uri_fn, body_fn)
    raise ValueError(f"no projector registered for doc_type={doc_type!r}")
