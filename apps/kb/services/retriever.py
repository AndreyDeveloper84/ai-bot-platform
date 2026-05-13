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

    # Cold collection — short-circuit. Saves an embedding call (≈ 200ms +
    # an OpenAI HTTP round-trip) when the tenant's KB is unbuilt.
    if chroma_client.collection_count(tenant) == 0:
        _emit(tenant, requested_k, actual_k, doc_types, hits=0)
        return RetrievalResult(
            hits=[],
            requested_k=requested_k,
            actual_k=actual_k,
            doc_types_filter=doc_types,
        )

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

    query_embedding = _sync_embed(provider, query_text, model=embedding_model)

    where = _build_where(doc_types)
    hits = chroma_client.query(
        tenant,
        query_embedding=query_embedding,
        k=actual_k,
        where=where,
    )

    _emit(tenant, requested_k, actual_k, doc_types, hits=len(hits))

    return RetrievalResult(
        hits=hits,
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
