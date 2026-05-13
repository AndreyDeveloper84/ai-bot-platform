"""Per-tenant ChromaDB client wrapper (DRF-560 / Sprint 7 / K2).

Thin façade over `chromadb.HttpClient` (prod) / `chromadb.PersistentClient`
(local dev / tests) that enforces per-tenant collection isolation.

### Collection-naming rule — Decision 8

Each tenant gets exactly one collection: ``f"tenant_{tenant.id.hex}"``.

* ``tenant.id`` is a UUID; ``hex`` is the 32-char no-dashes form.
* Result fits within ChromaDB's collection-name constraints
  (63 chars max, `[a-zA-Z0-9_-]`).
* Hex (vs slug or str(uuid)) is stable across Python versions and
  doesn't depend on the human-readable slug — slug changes do NOT
  re-key the collection.
* Cross-tenant isolation is structural: the client refuses to read /
  write a collection without resolving it through ``_collection_name``.

### HTTP vs embedded

Production uses ``CHROMA_HTTP_HOST`` / ``CHROMA_HTTP_PORT`` (`.env.example`
already has them) → `chromadb.HttpClient`. Bearer auth header is added
once ``CHROMA_AUTH_TOKEN`` lands in M4 (DRF-595).

Local dev and tests fall back to ``chromadb.PersistentClient`` rooted at
``BASE_DIR / ".chroma"`` so unit tests don't need a running container.

### Why a thin wrapper

Sprint 7 / K-track consumers all want the same five operations
(upsert / query / delete_document / delete_collection / count). Each
of them in a Sprint-7-call-site would re-derive the collection name,
re-build the embedding function plumbing, and risk skipping a tenant
isolation check. Wrap once, guarantee everywhere.

### Idempotency

* :meth:`upsert` — same id rewrites embedding+metadata (chromadb's
  `upsert` semantics; not `add`).
* :meth:`delete_collection` — swallows `NotFoundError` so the K11
  pre_delete signal can fire safely on a tenant whose KB was never
  ingested.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.conf import settings

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KbItem:
    """One item flowing into :meth:`ChromaClient.upsert`.

    ``embedding`` may be empty when the collection's embedding_function
    is configured to embed at write time. K4 (DRF-562) computes the
    embedding via :class:`apps.llm.providers.openai_provider.OpenAIProvider`
    and passes it pre-computed so the collection stays embedding-fn-free
    (HttpClient mode requires server-side embedding fns to be configured
    on every node — we side-step by always supplying our own).
    """

    id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KbHit:
    """One hit returned by :meth:`ChromaClient.query`."""

    id: str
    text: str
    score: float  # cosine similarity in [0, 1] — 1 = identical
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def collection_name_for_tenant(tenant: "Tenant | UUID | str") -> str:
    """Decision 8 — `tenant_<uuid_hex>`.

    Accepts a Tenant model, a UUID, or a stringified UUID. The hex form
    drops dashes so the result fits the [a-zA-Z0-9_-] charset within 63
    chars (we use 39).
    """
    if hasattr(tenant, "id"):
        tid = tenant.id  # type: ignore[union-attr]
    else:
        tid = tenant
    if isinstance(tid, str):
        tid = UUID(tid)
    if not isinstance(tid, UUID):  # pragma: no cover — defensive
        raise TypeError(f"expected Tenant | UUID | str, got {type(tid).__name__}")
    return f"tenant_{tid.hex}"


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _build_chromadb_client() -> Any:
    """Pick HttpClient (prod / staging) vs PersistentClient (local / tests).

    Cached at process scope — chromadb clients are heavy to construct
    (especially HttpClient with auth headers) and there's only ever
    one server endpoint per process.
    """
    import chromadb

    host = getattr(settings, "CHROMA_HTTP_HOST", "") or ""
    if host:
        port = int(getattr(settings, "CHROMA_HTTP_PORT", 8001))
        # M4 (DRF-595) ships Bearer-auth wiring. Until then HTTP client
        # falls back to unauthenticated mode — safe in our isolated
        # docker-compose network.
        token = getattr(settings, "CHROMA_AUTH_TOKEN", "") or ""
        client_kwargs: dict[str, Any] = {"host": host, "port": port}
        if token:
            from chromadb.config import Settings as ChromaSettings

            client_kwargs["settings"] = ChromaSettings(
                chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
                chroma_client_auth_credentials=token,
            )
        return chromadb.HttpClient(**client_kwargs)

    # Local / tests — persistent client rooted under BASE_DIR/.chroma.
    persist_root = getattr(settings, "BASE_DIR", None)
    if persist_root is None:
        from pathlib import Path

        persist_root = Path.cwd()
    persist_path = str(persist_root / ".chroma")
    return chromadb.PersistentClient(path=persist_path)


def reset_client_cache() -> None:
    """Clear BOTH cached layers — the underlying chromadb client AND
    the ChromaClient wrapper singleton.

    Without clearing the wrapper, ``get_chroma_client()`` keeps
    handing back a ChromaClient whose ``_client`` was constructed
    against an older settings.BASE_DIR — common in tests that
    re-point ``BASE_DIR`` to a new ``tmp_path`` per case. The wrapper
    binds its inner chromadb client at construction time, so clearing
    only the inner LRU isn't enough — the wrapper's stale ref keeps
    going to the old persist root.

    Production code never calls this; it's a pure test helper.
    """
    _build_chromadb_client.cache_clear()
    # Tests sometimes monkeypatch `get_chroma_client` to inject a fake
    # client. After such a swap the symbol may not be the @lru_cache-
    # wrapped function any more — fall through silently rather than
    # crashing the test's teardown.
    cache_clear = getattr(get_chroma_client, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ChromaClient:
    """Per-tenant ChromaDB wrapper.

    Tenant isolation is enforced via :meth:`_collection_name` — every
    operation passes through it, so a missing or wrong tenant argument
    raises before touching the underlying collection.

    Cosine similarity is the default distance metric: ``hnsw:space="cosine"``.
    Score is rescaled into [0, 1] (1 = perfect match) so the FAQ skill
    (F2 / DRF-589) can apply a simple confidence threshold (Decision 5
    `confidence < 0.5 → handoff`).
    """

    def __init__(self) -> None:
        self._client = _build_chromadb_client()

    @staticmethod
    def _collection_name(tenant: "Tenant | UUID | str") -> str:
        return collection_name_for_tenant(tenant)

    def get_or_create_collection(self, tenant: "Tenant | UUID | str") -> Any:
        """Return the tenant's collection, creating it lazily.

        Idempotent — repeated calls return the same chromadb collection
        object (chromadb caches by name).
        """
        name = self._collection_name(tenant)
        return self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, tenant: "Tenant | UUID | str", items: list[KbItem]) -> None:
        """Idempotent insert / update. Empty list → no-op."""
        if not items:
            return
        collection = self.get_or_create_collection(tenant)
        # chromadb 1.5+ requires non-empty metadata dicts.
        metadatas = [(i.metadata if i.metadata else {"_placeholder": "1"}) for i in items]
        collection.upsert(
            ids=[i.id for i in items],
            documents=[i.text for i in items],
            embeddings=[i.embedding for i in items],
            metadatas=metadatas,
        )

    def query(
        self,
        tenant: "Tenant | UUID | str",
        query_embedding: list[float],
        *,
        k: int = 3,
        where: dict[str, Any] | None = None,
    ) -> list[KbHit]:
        """Cosine top-k. Returns empty list when the collection is empty
        or ``k`` ≤ 0.

        ``where`` is the chromadb metadata filter expression
        (e.g. ``{"doc_type": "faq"}``).
        """
        if k <= 0:
            return []
        collection = self.get_or_create_collection(tenant)
        count = collection.count()
        if count == 0:
            return []
        actual_k = min(k, count)
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=actual_k,
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        ids = result["ids"][0]
        docs = result["documents"][0]
        dists = result["distances"][0]
        metas = result["metadatas"][0] if result.get("metadatas") else [{}] * len(ids)
        hits: list[KbHit] = []
        for i, hid in enumerate(ids):
            # cosine distance ∈ [0, 2] (chromadb) → similarity ∈ [-1, 1].
            # Clamp to [0, 1] — negative similarity isn't meaningful for
            # confidence thresholds in the FAQ retrieval flow.
            score = max(0.0, 1.0 - float(dists[i]))
            hits.append(
                KbHit(
                    id=hid,
                    text=docs[i],
                    score=score,
                    metadata=metas[i] or {},
                )
            )
        return hits

    def delete_document(
        self,
        tenant: "Tenant | UUID | str",
        *,
        doc_id: str | UUID,
    ) -> None:
        """Delete all chunks belonging to a KbDocument.

        Matches on the ``doc_id`` metadata key K4 sets on every chunk.
        Idempotent — missing doc_id is silently OK (no chunks to delete).
        """
        collection = self.get_or_create_collection(tenant)
        collection.delete(where={"doc_id": str(doc_id)})

    def delete_collection(self, tenant: "Tenant | UUID | str") -> None:
        """Drop the entire tenant collection. K11 signal-handler target.

        Swallows ``not-found`` errors so the pre_delete hook can fire
        safely on tenants whose KB was never ingested.
        """
        name = self._collection_name(tenant)
        try:
            self._client.delete_collection(name)
        except Exception as exc:  # noqa: BLE001 — chromadb raises diverse types
            # `NotFoundError`, `InvalidCollectionException`, `ValueError`
            # depending on chromadb version. All resolve to "collection
            # already absent" which is the desired post-condition.
            logger.info(
                "chromadb.delete_collection.absent tenant=%s name=%s err=%s",
                getattr(tenant, "id", tenant),
                name,
                exc.__class__.__name__,
            )

    def collection_count(self, tenant: "Tenant | UUID | str") -> int:
        """Item count in the tenant's collection. 0 when absent."""
        return self.get_or_create_collection(tenant).count()


@lru_cache(maxsize=1)
def get_chroma_client() -> ChromaClient:
    """Process-wide singleton accessor.

    Reuses the cached chromadb client under the hood; this exists so
    callers don't construct a new ``ChromaClient()`` per operation.
    """
    return ChromaClient()
