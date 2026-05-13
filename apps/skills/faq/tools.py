"""``search_knowledge_base`` tool (DRF-588 / Sprint 7 / F1).

Exposes the K5 retriever (DRF-563) as a function-callable tool the FAQ
skill (F2 / DRF-589) registers with the LLM. The tool spec ships in
the canonical OpenAI shape so the L3 (DRF-582) router can hand it to
either OpenAI or Anthropic without translation at the call site.

### Why a wrapper instead of letting F2 call ``search_kb`` directly

* **Cache layer**: identical queries within 5 minutes share a result.
  An LLM that decides to re-tool-call on the same question (multi-turn
  clarification, "give me more detail" loop) shouldn't re-embed the
  text twice. The cache key is the SHA-256 of
  ``tenant_id|query|sorted(doc_types)|k`` — Python's built-in
  ``hash()`` is salt-randomised per process, so cross-worker cache
  hits would never line up.
* **Audit trail**: every tool call writes an ``kb.search_invoked``
  audit row with the cache-hit flag. Forensic queries can correlate
  "FAQ replied X" → "we retrieved chunks Y" → "cache hit / fresh
  embed".
* **k clamping at the tool surface**: the LLM might emit `k=10` if
  the prompt drifts. The retriever silently clamps to 5 (Decision 6)
  but the tool surface also exposes the clamp so audit shows the
  caller's intent.

### Tool spec

The exported :data:`SEARCH_KB_TOOL_SPEC` is in the L1 / Decision-12
canonical form (``{"name", "description", "parameters"}``). L3
(DRF-582) wraps it into the SDK's ``tools=[{"type": "function",
"function": ...}]`` envelope on the way out to OpenAI / Anthropic.

### Cache invalidation

The K6 ingester (DRF-564) calls :func:`invalidate_kb_search_cache` on
every doc that lands chunks in ChromaDB so the next tool call rebuilds
fresh. Sprint 8 may switch to a per-doc cache namespace; Sprint 7 ships
a wholesale "clear all kb_search:* keys for this tenant" wipe — cheap
and correct for the single-tenant footprint.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.core.cache import cache

from apps.audit.services import write_audit
from apps.kb.services.retriever import search_kb
from apps.llm.protocol import LLMProvider

if TYPE_CHECKING:
    from apps.kb.chromadb_client import ChromaClient
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Canonical L1-shape tool spec. L3 (DRF-582) wraps for the SDK at
# call time. F2 (DRF-589) imports this and adds it to its `tools=[...]`.
SEARCH_KB_TOOL_SPEC: dict[str, Any] = {
    "name": "search_knowledge_base",
    "description": (
        "Search the salon's knowledge base for chunks relevant to the "
        "user's question. Returns up to k passages with provenance "
        "(source URI, doc type). Call when the user asks about prices, "
        "services, hours, contraindications, address, or other facts "
        "the brand-voice prompt instructed you to ground."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "User's question or paraphrase suitable for semantic search.",
            },
            "doc_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "service",
                        "master",
                        "contraindication",
                        "faq",
                        "help_article",
                        "legal",
                    ],
                },
                "description": "Optional whitelist of KB doc types to "
                "retrieve. Default: all six types.",
            },
            "k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "Number of chunks to return (clamped 1-5).",
            },
        },
        "required": ["query"],
    },
}


# Sprint 7 ships a 5-minute TTL on identical queries.
_CACHE_TTL_SECONDS = 5 * 60
_CACHE_KEY_PREFIX = "kb_search:"

EVENT_KB_SEARCH_INVOKED = "kb.search_invoked"


@dataclass(frozen=True)
class KbToolHit:
    """Tool-surface chunk. The shape the LLM sees.

    Trimmed compared to :class:`apps.kb.chromadb_client.KbHit` —
    no internal metadata leaks (``tenant_id``, ``checksum`` etc.); the
    LLM gets exactly what it needs to ground an answer + cite a source.
    """

    chunk_id: str
    text: str
    score: float
    doc_type: str = ""
    source_uri: str = ""


@dataclass(frozen=True)
class KbToolResult:
    """Return value of :func:`search_knowledge_base`.

    Fields:
      hits: parsed chunks ready for inclusion in the LLM context.
      cache_hit: True when the answer came from Redis (audit only).
      requested_k: the ``k`` the LLM requested.
      actual_k: post-clamp value the retriever queried with.
    """

    hits: list[KbToolHit] = field(default_factory=list)
    cache_hit: bool = False
    requested_k: int = 0
    actual_k: int = 0


def search_knowledge_base(
    tenant: "Tenant | UUID | str",
    query: str,
    *,
    provider: LLMProvider,
    doc_types: list[str] | None = None,
    k: int = 3,
    chroma: "ChromaClient | None" = None,
) -> KbToolResult:
    """Tool implementation — embed query, fetch top-k, cache, audit.

    Args:
      tenant: Tenant context. Cache key + retrieval scope.
      query: user question to embed.
      provider: :class:`apps.llm.protocol.LLMProvider` (L5 router supplies).
      doc_types: optional whitelist; FAQ skill defaults to
                 ``["faq", "help_article"]``.
      k: 1-5, clamped by the retriever per Decision 6.
      chroma: optional :class:`apps.kb.chromadb_client.ChromaClient`
              override — tests inject a per-test PersistentClient so
              the LRU singleton doesn't pin the wrong tmp_path between
              cases. Production callers leave this None.

    Returns:
      :class:`KbToolResult` ready for serialisation back to the LLM
      tool_result message.
    """
    tenant_id = _tenant_id_str(tenant)
    cache_key = _cache_key(tenant_id, query, doc_types, k)

    cached = cache.get(cache_key)
    if cached is not None:
        result = _from_cache_payload(cached)
        _audit_search_invoked(
            tenant_id=tenant_id,
            query=query,
            doc_types=doc_types,
            k=k,
            actual_k=result.actual_k,
            hits_count=len(result.hits),
            cache_hit=True,
        )
        return result

    retrieved = search_kb(
        tenant,
        query,
        provider=provider,
        k=k,
        doc_types=doc_types,
        chroma=chroma,
    )
    tool_hits = [
        KbToolHit(
            chunk_id=hit.id,
            text=hit.text,
            score=hit.score,
            doc_type=str(hit.metadata.get("doc_type", "")),
            source_uri=str(hit.metadata.get("source_uri", "")),
        )
        for hit in retrieved.hits
    ]
    result = KbToolResult(
        hits=tool_hits,
        cache_hit=False,
        requested_k=retrieved.requested_k,
        actual_k=retrieved.actual_k,
    )
    cache.set(cache_key, _to_cache_payload(result), timeout=_CACHE_TTL_SECONDS)

    _audit_search_invoked(
        tenant_id=tenant_id,
        query=query,
        doc_types=doc_types,
        k=k,
        actual_k=result.actual_k,
        hits_count=len(result.hits),
        cache_hit=False,
    )
    return result


# ---------------------------------------------------------------------------
# Cache plumbing
# ---------------------------------------------------------------------------


def _cache_key(tenant_id: str, query: str, doc_types: list[str] | None, k: int) -> str:
    """SHA-256-stable cache key.

    Python's built-in ``hash()`` is salt-randomised per process — a
    cache write from worker A would never hit on worker B. SHA-256 is
    deterministic so cross-worker hits work.

    Sorting ``doc_types`` so caller-order doesn't fragment the cache.
    """
    payload = f"{tenant_id}|{query}|{sorted(doc_types or [])}|{k}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{_CACHE_KEY_PREFIX}{digest}"


def _to_cache_payload(result: KbToolResult) -> str:
    """Serialise to a Redis-friendly JSON string."""
    return json.dumps(
        {
            "hits": [
                {
                    "chunk_id": h.chunk_id,
                    "text": h.text,
                    "score": h.score,
                    "doc_type": h.doc_type,
                    "source_uri": h.source_uri,
                }
                for h in result.hits
            ],
            "requested_k": result.requested_k,
            "actual_k": result.actual_k,
        }
    )


def _from_cache_payload(raw: str) -> KbToolResult:
    data = json.loads(raw)
    return KbToolResult(
        hits=[KbToolHit(**hit) for hit in data["hits"]],
        cache_hit=True,
        requested_k=data["requested_k"],
        actual_k=data["actual_k"],
    )


def invalidate_kb_search_cache(tenant: "Tenant | UUID | str") -> None:
    """Wipe cached search results for ``tenant``.

    Sprint 7 ships a coarse cache — we don't have per-doc invalidation,
    so the K6 ingester signals "this tenant's content changed; drop
    all of its cached search results". The single-tenant footprint
    makes this cheap.

    The Django cache backend doesn't support prefix-delete portably
    (Redis does via ``SCAN``; memcached doesn't). Sprint 7 uses the
    documented ``cache.delete_many`` over an explicit set when the
    backend supports it; for the default Redis backend with
    ``django-redis`` we use the ``delete_pattern`` extension.
    """
    backend = getattr(cache, "delete_pattern", None)
    if callable(backend):
        # django-redis: prefix wildcard.
        backend(f"{_CACHE_KEY_PREFIX}*")
        logger.info("kb.tool.cache_invalidated tenant=%s", _tenant_id_str(tenant))
        return
    # Test backends (locmem) — no pattern delete. Best effort: clear the
    # whole cache. Tests run in isolated cache scopes so this is safe.
    cache.clear()
    logger.info(
        "kb.tool.cache_invalidated tenant=%s mode=full_clear",
        _tenant_id_str(tenant),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tenant_id_str(tenant: "Tenant | UUID | str") -> str:
    """Render the tenant id as a stable string for cache + audit."""
    tid = getattr(tenant, "id", tenant)
    return str(tid)


def _audit_search_invoked(
    *,
    tenant_id: str,
    query: str,
    doc_types: list[str] | None,
    k: int,
    actual_k: int,
    hits_count: int,
    cache_hit: bool,
) -> None:
    """Forensic audit row. Hashes the query so PII never lands in payload."""
    query_hash = hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:16]
    write_audit(
        EVENT_KB_SEARCH_INVOKED,
        target="KbSearch",
        payload={
            "tenant_id": tenant_id,
            "query_hash": query_hash,
            "doc_types": doc_types or [],
            "requested_k": k,
            "actual_k": actual_k,
            "hits_count": hits_count,
            "cache_hit": cache_hit,
        },
    )
