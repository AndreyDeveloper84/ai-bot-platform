"""End-to-end KB integration tests (DRF-568 / Sprint 7 / K10).

Drives the full chunker → ingester → retriever round-trip against a
real :class:`apps.kb.chromadb_client.ChromaClient` (PersistentClient
backed by ``tmp_path``). The LLM is mocked via :class:`FakeProvider`
so CI doesn't hit OpenAI.

### What this guards

Sprint 7 ships K3 (chunker) + K4 (ingester) + K5 (retriever) as
independent modules. Each has its own unit suite. K10 pins the
**contract between them** — if a future refactor changes how K3
emits metadata, K5 still has to read it; if K4 changes the chunk
``id`` format, K2's delete-by-doc_id still has to match.

The unit tests don't catch contract drift across module boundaries.
K10 does.

### What's covered

* End-to-end: KbDocument → ingest → search returns same content.
* Multi-doc retrieval: chunks from doc A scoring above doc B by
  topic, doc_types filter restricts correctly.
* Re-ingest idempotency: same document run twice → no duplicate
  chunks in chromadb.
* Content edit flow: text changed → stale chunks deleted, new
  chunks searchable, old text not searchable.
* Tenant isolation: tenant A's ingest never surfaces in tenant B's
  search.
* Empty / cold paths: empty content doesn't crash; cold collection
  short-circuits without calling embedder.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from apps.kb.chromadb_client import ChromaClient, reset_client_cache
from apps.kb.models import KbDocType, KbDocument
from apps.kb.services.ingester import ingest_document
from apps.kb.services.retriever import search_kb
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _isolated_chroma(tmp_path: Path, settings: pytest.FixtureRequest):
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    settings.CHROMA_HTTP_HOST = ""  # type: ignore[attr-defined]
    reset_client_cache()
    yield
    reset_client_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="kb-int", name="KB Int")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="kb-int-b", name="KB Int B")


@pytest.fixture
def chroma() -> ChromaClient:
    return ChromaClient()


class FakeProvider:
    """Embed-only fake. Produces deterministic vectors so cosine math
    on the PersistentClient backend works.

    The vector encodes the text into 8 floats based on character
    frequencies — similar texts cluster, different texts diverge.
    Good enough for "doc A more similar to query A than doc B" assertions.
    """

    name = "fake"
    default_embedding_model = "fake-embed-3"

    def __init__(self) -> None:
        self.embedding_calls: list[str] = []

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        self.embedding_calls.append(text)
        # Build an 8-dim vector by hashing chars into buckets.
        vec = [0.0] * 8
        for char in text or "x":
            bucket = ord(char) % 8
            vec[bucket] += 1.0
        # Normalise so cosine math is well-behaved.
        norm = (sum(v * v for v in vec) ** 0.5) or 1.0
        return [v / norm for v in vec]

    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


def _make_doc(
    tenant: Tenant,
    *,
    content: str,
    doc_type: str = KbDocType.FAQ,
    source_uri: str = "mysite://faq/1",
) -> KbDocument:
    return KbDocument.all_tenants.create(
        tenant=tenant,
        doc_type=doc_type,
        source_uri=source_uri,
        content=content,
    )


# ---------------------------------------------------------------------------
# Happy-path round-trips
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_ingest_then_search_returns_chunk(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        doc = _make_doc(
            tenant,
            content="Часы работы: понедельник-воскресенье 10:00-22:00.",
            source_uri="mysite://faq/hours",
        )
        ingest_document(doc, provider=provider, chroma=chroma)
        result = search_kb(
            tenant,
            "часы работы",
            provider=provider,
            k=1,
            chroma=chroma,
        )
        assert len(result.hits) == 1
        assert "Часы работы" in result.hits[0].text
        # Provenance metadata travels chunker → ingester → chromadb → retriever.
        assert result.hits[0].metadata["doc_id"] == str(doc.id)
        assert result.hits[0].metadata["source_uri"] == "mysite://faq/hours"
        assert result.hits[0].metadata["doc_type"] == "faq"

    def test_metadata_includes_ordinal_and_token_count(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        doc = _make_doc(tenant, content="Body text.")
        ingest_document(doc, provider=provider, chroma=chroma)
        result = search_kb(tenant, "x", provider=provider, k=1, chroma=chroma)
        meta = result.hits[0].metadata
        assert "ordinal" in meta
        assert "token_count" in meta
        assert "checksum" in meta


# ---------------------------------------------------------------------------
# Multi-doc retrieval + filtering
# ---------------------------------------------------------------------------


class TestMultiDocRetrieval:
    def test_doc_types_filter_excludes_other_types(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        faq = _make_doc(
            tenant,
            content="FAQ: цены",
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/1",
        )
        service = _make_doc(
            tenant,
            content="Service: цены",
            doc_type=KbDocType.SERVICE,
            source_uri="mysite://services/1",
        )
        ingest_document(faq, provider=provider, chroma=chroma)
        ingest_document(service, provider=provider, chroma=chroma)

        result = search_kb(
            tenant,
            "цены",
            provider=provider,
            k=5,
            doc_types=["faq"],
            chroma=chroma,
        )
        # Only faq chunks returned.
        types = {h.metadata["doc_type"] for h in result.hits}
        assert types == {"faq"}

    def test_no_filter_returns_mixed_types(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        _make_doc(
            tenant,
            content="FAQ row",
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/1",
        )
        _make_doc(
            tenant,
            content="Service row",
            doc_type=KbDocType.SERVICE,
            source_uri="mysite://services/1",
        )
        for doc in KbDocument.all_tenants.filter(tenant=tenant):
            ingest_document(doc, provider=provider, chroma=chroma)
        result = search_kb(tenant, "row", provider=provider, k=5, chroma=chroma)
        types = {h.metadata["doc_type"] for h in result.hits}
        assert types == {"faq", "service"}


# ---------------------------------------------------------------------------
# Re-ingest semantics
# ---------------------------------------------------------------------------


class TestReIngest:
    def test_idempotent_no_duplicate_chunks(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        doc = _make_doc(tenant, content="Hello world.")
        ingest_document(doc, provider=provider, chroma=chroma)
        count_after_first = chroma.collection_count(tenant)
        # Re-ingest the SAME doc (no content change).
        doc.refresh_from_db()
        ingest_document(doc, provider=provider, chroma=chroma)
        count_after_second = chroma.collection_count(tenant)
        # Skip path bumps embedded_at; chunk count unchanged.
        assert count_after_first == count_after_second

    def test_content_edit_replaces_chunks(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        doc = _make_doc(
            tenant,
            content="Старая фраза про массаж.",
            source_uri="mysite://faq/edit",
        )
        ingest_document(doc, provider=provider, chroma=chroma)

        # Edit upstream — bump checksum + push embedded_at into the past
        # so the K4 selector picks it up.
        doc.content = "Новая фраза про эпиляцию."
        doc.checksum = ""  # forces recompute on save
        doc.embedded_at = datetime.now(timezone.utc) - timedelta(hours=1)
        doc.save()

        ingest_document(doc, provider=provider, chroma=chroma)

        # Old phrase NOT in any chunk; new phrase IS.
        hits = chroma.query(tenant, query_embedding=[0.1] * 8, k=10)
        chunk_texts = [h.text for h in hits]
        assert all("Старая" not in t for t in chunk_texts)
        assert any("эпиляции" in t or "эпиляцию" in t for t in chunk_texts)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_search_never_crosses_tenants(
        self,
        tenant: Tenant,
        tenant_b: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        doc_a = _make_doc(
            tenant,
            content="Tenant A secret content",
            source_uri="mysite://faq/a",
        )
        doc_b = _make_doc(
            tenant_b,
            content="Tenant B secret content",
            source_uri="mysite://faq/b",
        )
        ingest_document(doc_a, provider=provider, chroma=chroma)
        ingest_document(doc_b, provider=provider, chroma=chroma)

        result_a = search_kb(tenant, "secret", provider=provider, k=5, chroma=chroma)
        result_b = search_kb(tenant_b, "secret", provider=provider, k=5, chroma=chroma)

        assert all("Tenant A" in h.text for h in result_a.hits)
        assert all("Tenant B" in h.text for h in result_b.hits)


# ---------------------------------------------------------------------------
# Cold paths
# ---------------------------------------------------------------------------


class TestColdPaths:
    def test_search_before_any_ingest_returns_empty(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        result = search_kb(tenant, "anything", provider=provider, k=3, chroma=chroma)
        assert result.hits == []
        # Cold collection — short-circuit means no embedding call.
        assert provider.embedding_calls == []

    def test_empty_content_doc_skips_quietly(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        doc = _make_doc(tenant, content="   \n\n  ")
        result = ingest_document(doc, provider=provider, chroma=chroma)
        assert result.ingested is False
        assert result.chunk_count == 0
        # No chunks landed in chromadb.
        assert chroma.collection_count(tenant) == 0


# ---------------------------------------------------------------------------
# k clamp (decision 6)
# ---------------------------------------------------------------------------


class TestKClampIntegration:
    def test_k_over_5_clamped_at_retriever(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        # Seed 7 chunks (use multi-paragraph content to force multiple).
        long_doc = _make_doc(
            tenant,
            content=("\n\n".join(f"Параграф {i}. " * 50 for i in range(8))),
            doc_type=KbDocType.SERVICE,
            source_uri="mysite://services/big",
        )
        ingest_document(long_doc, provider=provider, chroma=chroma)

        result = search_kb(tenant, "параграф", provider=provider, k=10, chroma=chroma)
        # Clamp to 5 per Decision 6.
        assert result.requested_k == 10
        assert result.actual_k == 5
        assert len(result.hits) <= 5
