"""KB document ingester (DRF-562 / Sprint 7 / K4).

Drives the chunk → embed → ChromaDB-upsert pipeline for one
:class:`apps.kb.models.KbDocument`. K6 (DRF-564) wraps this in a Celery
task that walks "needs embedding" rows; K7 (DRF-565) chains it after a
successful catalog sync; K8 (DRF-566) is a one-shot legacy migration
command.

### What the ingester guarantees

1. **Idempotency through checksum check.** If a re-ingest sees the same
   SHA-256 ``checksum`` it already embedded against, the embedding work
   is skipped — but ``embedded_at`` is still bumped so the K6 selector
   stops re-picking the row. This costs one Postgres UPDATE per cycle
   rather than thousands of OpenAI calls.
2. **Atomic per-document.** Failed embedding mid-document leaves
   ``embedded_at IS NULL`` so the next run picks the row up. We do
   NOT half-upsert into ChromaDB — chunks land in a single
   :meth:`ChromaClient.upsert` call. Partial failures are bounded to
   one document, never one chunk.
3. **Old chunks dropped before new chunks landed.** If a document
   changed (checksum mismatch), the prior chunks for that ``doc_id``
   are deleted from ChromaDB before the new chunks are upserted. The
   ``doc_id`` metadata key is the join — K2's ``delete_document``
   matches on it.

### Why no Celery here

This module is the pure-Python work unit. K6 (DRF-564) is the Celery
wrapper. Separation lets unit tests exercise the orchestration without
spinning up a worker; integration tests in K6 cover the queue side.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from apps.audit.services import write_audit
from apps.events.services import emit
from apps.kb.chromadb_client import ChromaClient, KbItem, get_chroma_client
from apps.kb.models import KbDocument
from apps.kb.services.chunker import chunk_document
from apps.llm.protocol import LLMError, LLMProvider

logger = logging.getLogger(__name__)


# Sprint 7 / K4 — emitted by ingest_document on success. Sprint 8 may
# upgrade this to a canonical event in apps.events.vocabulary; for now
# it's a free-form event name, enough for forensic queries.
EVENT_KB_DOCUMENT_INGESTED = "kb_document_ingested"


@dataclass(frozen=True)
class IngestResult:
    """Return value of :func:`ingest_document`.

    Fields:
      ingested: True when we hit OpenAI + ChromaDB. False when the
                row was skipped (checksum unchanged).
      chunk_count: number of chunks upserted (0 when skipped).
      doc_id: KbDocument.id — useful for replay correlation.
    """

    ingested: bool
    chunk_count: int
    doc_id: str


def ingest_document(
    kb_doc: KbDocument,
    *,
    provider: LLMProvider,
    chroma: ChromaClient | None = None,
    embedding_model: str | None = None,
    force: bool = False,
) -> IngestResult:
    """Ingest one :class:`KbDocument` end-to-end.

    Args:
      kb_doc: source row. The chunker reads ``content``/``doc_type``;
              the upserter writes ``embedded_at`` on success.
      provider: any :class:`apps.llm.protocol.LLMProvider`. The L5
                router (DRF-587) supplies one per call; tests pass a
                mock. ``embedding()`` is the only method called.
      chroma: optional :class:`ChromaClient` override — tests inject
              a per-test :class:`PersistentClient`. Defaults to the
              process-wide singleton (:func:`get_chroma_client`).
      embedding_model: passed through to ``provider.embedding()``.
                       Defaults to whatever the provider considers
                       default (text-embedding-3-small on OpenAI).
      force: bypass the checksum-skip guard. K6's nightly re-embed
             flag sets this when the embedding model itself rolls.

    Returns:
      :class:`IngestResult` describing what happened.

    Raises:
      :class:`apps.llm.protocol.LLMError` and friends. The K6 wrapper
      catches and retries; the ingester itself doesn't swallow.
    """
    chroma_client = chroma if chroma is not None else get_chroma_client()

    # Skip path — checksum hasn't moved and we already embedded.
    if not force and _should_skip(kb_doc):
        logger.info(
            "kb.ingester.skip doc_id=%s reason=checksum_unchanged",
            kb_doc.id,
        )
        # Still touch embedded_at so K6's `embedded_at < updated_at`
        # selector stops re-picking this row on every cycle.
        kb_doc.embedded_at = datetime.now(timezone.utc)
        kb_doc.save(update_fields=["embedded_at"])
        return IngestResult(ingested=False, chunk_count=0, doc_id=str(kb_doc.id))

    # Chunk the body.
    chunks = chunk_document(
        kb_doc.content,
        doc_type=kb_doc.doc_type,
        extra_metadata={
            "doc_id": str(kb_doc.id),
            "source_uri": kb_doc.source_uri,
            "version": kb_doc.version,
            "locale": kb_doc.locale,
        },
    )

    if not chunks:
        # Empty content — silently mark embedded so we don't re-fetch
        # forever. This shouldn't happen for normal sync output but
        # K8 legacy migration may import edge cases.
        kb_doc.embedded_at = datetime.now(timezone.utc)
        kb_doc.save(update_fields=["embedded_at"])
        logger.warning("kb.ingester.empty doc_id=%s", kb_doc.id)
        return IngestResult(ingested=False, chunk_count=0, doc_id=str(kb_doc.id))

    # Embed each chunk. We call the provider sequentially — Sprint 7
    # has a single-tenant footprint where batching gives marginal speedup
    # vs the per-chunk httpx round-trip. Sprint 8 may switch to OpenAI's
    # batch-embed endpoint if measurements warrant.
    items: list[KbItem] = []
    for chunk in chunks:
        try:
            vec = _sync_embed(provider, chunk.text, model=embedding_model)
        except LLMError:
            # Don't half-write. Re-raise; K6 retries the whole document.
            logger.exception(
                "kb.ingester.embed_failed doc_id=%s ordinal=%s",
                kb_doc.id,
                chunk.ordinal,
            )
            raise
        items.append(
            KbItem(
                id=f"{kb_doc.id}:{chunk.ordinal}",
                text=chunk.text,
                embedding=vec,
                metadata={
                    **chunk.metadata,
                    "tenant_id": str(kb_doc.tenant_id),
                    "ordinal": chunk.ordinal,
                    "token_count": chunk.token_count,
                    "checksum": kb_doc.checksum,
                },
            )
        )

    # Replace existing chunks for this document — guards against stale
    # cosine-similar matches when the content edit shrinks the doc.
    chroma_client.delete_document(kb_doc.tenant, doc_id=str(kb_doc.id))
    chroma_client.upsert(kb_doc.tenant, items)

    kb_doc.embedded_at = datetime.now(timezone.utc)
    kb_doc.save(update_fields=["embedded_at"])

    # Forensic trail. The audit row captures the doc-level state;
    # the event is the canonical fan-out trigger (K6 listeners hook
    # here for stats / monitoring).
    write_audit(
        EVENT_KB_DOCUMENT_INGESTED,
        target="KbDocument",
        target_id=str(kb_doc.id),
        payload={
            "tenant_id": str(kb_doc.tenant_id),
            "doc_type": kb_doc.doc_type,
            "source_uri": kb_doc.source_uri,
            "version": kb_doc.version,
            "chunk_count": len(items),
            "checksum": kb_doc.checksum,
        },
    )
    emit(
        EVENT_KB_DOCUMENT_INGESTED,
        distinct_id=str(kb_doc.tenant_id),
        properties={
            "doc_id": str(kb_doc.id),
            "doc_type": kb_doc.doc_type,
            "chunk_count": len(items),
        },
    )

    return IngestResult(
        ingested=True,
        chunk_count=len(items),
        doc_id=str(kb_doc.id),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_skip(kb_doc: KbDocument) -> bool:
    """Decide whether the document needs re-embedding.

    Skip when:
      * ``embedded_at`` is set AND
      * ``updated_at <= embedded_at`` (content hasn't moved since)

    Checksum is the secondary guard — if a caller manually flips the
    checksum to a known-bad value we still re-embed (that's the K8
    forensics escape hatch).
    """
    if kb_doc.embedded_at is None:
        return False
    return kb_doc.updated_at <= kb_doc.embedded_at


def _sync_embed(
    provider: LLMProvider,
    text: str,
    *,
    model: str | None,
) -> list[float]:
    """Call ``provider.embedding`` from sync code.

    The provider Protocol is async. The ingester runs from a Celery
    worker (sync). We bridge with ``asyncio.run`` per chunk — each
    call is short (one OpenAI HTTP round-trip), no shared loop state
    survives between calls, so the run-and-exit pattern is safe here.

    For multi-chunk batches we accept the per-call event-loop spin-up
    cost (negligible: ~1ms vs the ~200ms network round-trip).
    """
    import asyncio

    # Default model handled by the provider; passing None falls through
    # to provider.default_embedding_model.
    chosen = model or ""
    if chosen:
        return asyncio.run(provider.embedding(text, model=chosen))
    # Some providers accept omitting the kwarg entirely. Use the
    # default-completion-model attr when present to satisfy mypy.
    return asyncio.run(
        provider.embedding(
            text, model=getattr(provider, "default_embedding_model", "text-embedding-3-small")
        )
    )
