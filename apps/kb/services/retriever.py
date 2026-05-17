"""KB retrieval entry point (DRF-563 / Sprint 7 / K5).

The single function the FAQ skill (F2 / DRF-589) calls when it wants
chunks for grounded answering. F1 (DRF-588) wraps this as an
OpenAI-callable tool, but synchronous skills (including Sprint 8+
booking flows) may want to call :func:`search_kb` directly.

### k clamping — Decision 6

``k`` is hard-clamped to ``[1, 5]``:

* ``k > 5`` is silently rounded down and **logged** so a misconfigured
  caller surfaces in observability. We don't raise — a confused LLM
  asking for 10 chunks should still get an answer.
* ``k < 1`` is treated as ``k=1`` (the cheapest meaningful retrieval).

Why 5: latency + prompt-token math at the F2 layer. Five 512-token
chunks fit in the working window without crowding out the user's
message + voice examples; ten chunks blow the budget.

### Empty / cold collection

When the tenant has no embeddings yet (or the collection was deleted
by K11), :func:`search_kb` returns ``[]`` rather than raising. The
FAQ skill reads the empty list as "low confidence" and routes to
post-skill handoff (O2 step 10.5).

### Global-fallback for shared doc_types — Sub-3 / GH #116

For ``doc_type ∈ {service, contraindication, help_article}`` the
retriever issues a *second* ChromaDB query against the ``global_kb``
system tenant's collection (looked up by ``slug == "global_kb"``).
Results are merged by cosine score descending and truncated to the
caller's ``k``. Each returned chunk carries ``metadata["kb_source"]``
of ``"tenant"`` or ``"global"`` so replay / debugging can trace
provenance.

**Security invariant**: ``doc_type ∈ {master, faq, legal}`` is
strictly per-tenant — masters belong to a salon, FAQs are salon-
specific, and legal text is the tenant's own juridical entity. The
global collection is NEVER queried for these doc_types; leakage would
be a tenancy breach. The branch is guarded by
:data:`_GLOBAL_FALLBACK_DOC_TYPES` and asserted in tests.

Graceful degradation: if the ``global_kb`` tenant row is missing
(Sub-2 hasn't seeded it, dev environment, etc.) we log a single WARN
and fall back to tenant-only results — never raise.

### Event emission

Every call emits `kb_retrieval_performed` with the tenant_id, k,
doc_types filter, and the resulting hit count. Forensic only — no
PII in the payload (query string is not logged here; chunk metadata
travels through the redactor in K13 / DRF-571).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING
from uuid import UUID

from apps.events.services import emit
from apps.kb.chromadb_client import ChromaClient, KbHit, get_chroma_client
from apps.llm.protocol import LLMProvider

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Decision 6 — clamp bounds.
_MIN_K = 1
_MAX_K = 5

EVENT_KB_RETRIEVAL_PERFORMED = "kb_retrieval_performed"

# Sub-3 / GH #116 — doc_types that get the global_kb fallback.
# MASTER / FAQ / LEGAL are deliberately excluded; cross-tenant query
# on those is a tenancy isolation breach.
_GLOBAL_FALLBACK_DOC_TYPES: frozenset[str] = frozenset(
    {
        "service",
        "contraindication",
        "help_article",
    }
)

# Slug of the system tenant that owns the shared (cross-salon) KB
# content. The lookup deliberately uses slug rather than a future
# ``is_system`` boolean — Sub-1 (PR #120) is parallel work and we
# don't want a hard dependency on its schema change.
_GLOBAL_KB_TENANT_SLUG = "global_kb"


@dataclass(frozen=True)
class RetrievalResult:
    """Bundle returned by :func:`search_kb`.

    Fields:
      hits: top-k :class:`KbHit` instances, ordered by descending score.
      requested_k: the ``k`` the caller asked for (pre-clamp).
      actual_k: the ``k`` we actually queried with (post-clamp).
      doc_types_filter: doc_types the caller restricted to (or None).
    """

    hits: list[KbHit] = field(default_factory=list)
    requested_k: int = 0
    actual_k: int = 0
    doc_types_filter: list[str] | None = None


def search_kb(
    tenant: "Tenant | UUID | str",
    query: str,
    *,
    provider: LLMProvider,
    k: int = 3,
    doc_types: list[str] | None = None,
    chroma: ChromaClient | None = None,
    embedding_model: str | None = None,
) -> RetrievalResult:
    """Embed ``query`` and return the top-``k`` chunks for ``tenant``.

    Args:
      tenant: Tenant object, UUID, or str-UUID. The chromadb client
              resolves the per-tenant collection by hex.
      query: user-facing question — embedded via ``provider.embedding``.
      provider: any :class:`apps.llm.protocol.LLMProvider`. F2 passes
                whatever the L5 router picked for this skill turn.
      k: requested top-k. Silently clamped to [1, 5] per Decision 6.
      doc_types: optional whitelist. FAQ skill typically restricts to
                 ``["faq", "help_article"]`` so service-description
                 chunks don't drown out the answer.
      chroma: optional :class:`ChromaClient` override for tests.
      embedding_model: passed through to ``provider.embedding``.

    Returns:
      :class:`RetrievalResult`. Empty ``hits`` is a legitimate value;
      the caller (F2) reads it as a low-confidence signal.
    """
    requested_k = int(k)
    actual_k = _clamp_k(requested_k)

    chroma_client = chroma if chroma is not None else get_chroma_client()

    # Cold collection — short-circuit if BOTH tenant and (potentially)
    # global_kb are empty. We still allow the global fallback to run
    # below when the local tenant is cold but the global collection
    # holds shared content (services / contraindications / help).
    tenant_count = chroma_client.collection_count(tenant)

    query_text = (query or "").strip()
    if not query_text:
        # Empty query — no embedding to compute, no hits to return.
        _emit(tenant, requested_k, actual_k, doc_types, hits=0)
        return RetrievalResult(
            hits=[],
            requested_k=requested_k,
            actual_k=actual_k,
            doc_types_filter=doc_types,
        )

    # Resolve the global tenant up-front so we can also short-circuit
    # when both collections are cold.
    global_tenant = _get_global_kb_tenant() if _should_use_global_fallback(doc_types) else None
    global_count = chroma_client.collection_count(global_tenant) if global_tenant is not None else 0

    if tenant_count == 0 and global_count == 0:
        _emit(tenant, requested_k, actual_k, doc_types, hits=0)
        return RetrievalResult(
            hits=[],
            requested_k=requested_k,
            actual_k=actual_k,
            doc_types_filter=doc_types,
        )

    query_embedding = _sync_embed(provider, query_text, model=embedding_model)
    where = _build_where(doc_types)

    tenant_hits: list[KbHit] = []
    if tenant_count > 0:
        tenant_hits = chroma_client.query(
            tenant,
            query_embedding=query_embedding,
            k=actual_k,
            where=where,
        )

    global_hits: list[KbHit] = []
    if global_tenant is not None and global_count > 0:
        global_hits = chroma_client.query(
            global_tenant,
            query_embedding=query_embedding,
            k=actual_k,
            where=where,
        )

    merged = _merge_hits(tenant_hits, global_hits, actual_k)

    _emit(tenant, requested_k, actual_k, doc_types, hits=len(merged))

    return RetrievalResult(
        hits=merged,
        requested_k=requested_k,
        actual_k=actual_k,
        doc_types_filter=doc_types,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp_k(k: int) -> int:
    """Apply Decision 6 — silent clamp + log on overflow."""
    if k > _MAX_K:
        logger.warning(
            "kb.retriever.k_clamped requested=%s capped=%s reason=decision_6",
            k,
            _MAX_K,
        )
        return _MAX_K
    if k < _MIN_K:
        return _MIN_K
    return k


def _build_where(doc_types: list[str] | None) -> dict[str, object] | None:
    """Translate the doc_types whitelist into a chromadb where-clause.

    chromadb expects ``{"doc_type": {"$in": [...]}}`` for multi-value
    filters; single-value falls back to direct equality which is fine
    but the ``$in`` form is uniform.
    """
    if not doc_types:
        return None
    return {"doc_type": {"$in": list(doc_types)}}


def _sync_embed(provider: LLMProvider, text: str, *, model: str | None) -> list[float]:
    """Sync bridge to async provider.embedding — same shape as K4."""
    if model:
        return asyncio.run(provider.embedding(text, model=model))
    return asyncio.run(
        provider.embedding(
            text,
            model=getattr(provider, "default_embedding_model", "text-embedding-3-small"),
        )
    )


def _should_use_global_fallback(doc_types: list[str] | None) -> bool:
    """Return True iff the caller's doc_type filter is exclusively in
    the global-fallback whitelist (``service`` / ``contraindication`` /
    ``help_article``).

    Behaviour matrix:
      * ``None`` (no filter) → ``False``. The default FAQ flow scopes by
        doc_type explicitly; an unfiltered call could include sensitive
        types (``master`` / ``faq`` / ``legal``) where leakage is a
        breach. Conservative-by-default.
      * Any sensitive doc_type present → ``False``. Mixed queries that
        include MASTER/FAQ/LEGAL stay strictly per-tenant. Splitting
        such a query into two passes is overkill for current callers;
        if a future caller actually needs that, it can fan-out itself.
      * All listed doc_types are in the whitelist → ``True``.
    """
    if not doc_types:
        return False
    return all(dt in _GLOBAL_FALLBACK_DOC_TYPES for dt in doc_types)


@lru_cache(maxsize=1)
def _get_global_kb_tenant() -> "Tenant | None":
    """Resolve the system tenant that owns shared cross-salon KB content.

    Looked up by ``slug == "global_kb"`` (not ``is_system=True``) — Sub-1
    is parallel work and we don't want a hard schema dependency. Cached
    process-wide because the row is created once and never mutated.

    Returns ``None`` (with a WARN log) when the row doesn't exist yet —
    Sub-2 seeds it; in dev / fresh CI the row may be absent. Callers
    treat ``None`` as "no global fallback available" and serve
    tenant-only results.
    """
    # Local import to avoid Django app-loading order issues at module
    # import time (apps.kb may be imported before apps.tenancy in some
    # contexts).
    from apps.tenancy.models import Tenant

    row = Tenant.all_objects.filter(slug=_GLOBAL_KB_TENANT_SLUG).first()
    if row is None:
        logger.warning(
            "kb.retriever.global_kb_tenant_missing slug=%s falling_back_to_tenant_only",
            _GLOBAL_KB_TENANT_SLUG,
        )
    return row


def _merge_hits(
    tenant_hits: list[KbHit],
    global_hits: list[KbHit],
    k: int,
) -> list[KbHit]:
    """Tag provenance, merge by score descending, truncate to k.

    Each returned KbHit is a fresh dataclass instance with
    ``metadata["kb_source"]`` set to ``"tenant"`` or ``"global"``. The
    originals are immutable (``frozen=True``) so we rebuild rather than
    mutate.
    """
    tagged: list[KbHit] = []
    for hit in tenant_hits:
        tagged.append(_with_source(hit, "tenant"))
    for hit in global_hits:
        tagged.append(_with_source(hit, "global"))

    tagged.sort(key=lambda h: h.score, reverse=True)
    return tagged[:k]


def _with_source(hit: KbHit, source: str) -> KbHit:
    """Return a copy of ``hit`` with ``metadata["kb_source"] = source``.

    KbHit is frozen; we rebuild the dataclass to inject provenance
    without mutating the chromadb-owned metadata dict in place.
    """
    new_metadata = dict(hit.metadata or {})
    new_metadata["kb_source"] = source
    return KbHit(
        id=hit.id,
        text=hit.text,
        score=hit.score,
        metadata=new_metadata,
    )


def _emit(
    tenant: "Tenant | UUID | str",
    requested_k: int,
    actual_k: int,
    doc_types: list[str] | None,
    *,
    hits: int,
) -> None:
    tenant_id = getattr(tenant, "id", tenant)
    emit(
        EVENT_KB_RETRIEVAL_PERFORMED,
        distinct_id=str(tenant_id),
        properties={
            "requested_k": requested_k,
            "actual_k": actual_k,
            "doc_types": doc_types or [],
            "hit_count": hits,
        },
    )
