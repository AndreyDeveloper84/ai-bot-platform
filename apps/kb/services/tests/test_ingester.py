"""KB ingester tests (DRF-562 / Sprint 7 / K4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from apps.kb.chromadb_client import ChromaClient, reset_client_cache
from apps.kb.models import KbDocType, KbDocument
from apps.kb.services.ingester import (
    EVENT_KB_DOCUMENT_INGESTED,
    IngestResult,
    ingest_document,
)
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
    return Tenant.objects.create(slug="kb-ingest", name="KB Ingest")


@pytest.fixture
def chroma() -> ChromaClient:
    return ChromaClient()


class FakeProvider:
    """Minimal LLMProvider stub. Returns a deterministic vector per text
    so cross-call assertions on idempotency work.
    """

    name = "fake"
    # DRF-1437: the LLMProvider Protocol now requires a published
    # default completion model — the router's quota fallback has to
    # name a model the TARGET vendor understands when it hops.
    default_completion_model = "fake-model"

    def __init__(self) -> None:
        self.embedding_calls: list[tuple[str, str | None]] = []

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        self.embedding_calls.append((text, model))
        # 8-dim deterministic vector — sufficient for cosine on
        # PersistentClient backend.
        return [((ord(text[i % len(text)]) if text else 1) % 100) / 100.0 for i in range(8)]

    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def kb_doc(tenant: Tenant) -> KbDocument:
    return KbDocument.all_tenants.create(
        tenant=tenant,
        doc_type=KbDocType.FAQ,
        source_uri="mysite://faq/1",
        content="Часы работы: понедельник-воскресенье 10:00-22:00.",
    )


class TestHappyPath:
    def test_ingests_and_marks_embedded(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        result = ingest_document(kb_doc, provider=provider, chroma=chroma)
        assert isinstance(result, IngestResult)
        assert result.ingested is True
        assert result.chunk_count >= 1
        kb_doc.refresh_from_db()
        assert kb_doc.embedded_at is not None

    def test_chunks_landed_in_chromadb(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
        tenant: Tenant,
    ) -> None:
        ingest_document(kb_doc, provider=provider, chroma=chroma)
        assert chroma.collection_count(tenant) >= 1

    def test_chunk_metadata_carries_provenance(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
        tenant: Tenant,
    ) -> None:
        ingest_document(kb_doc, provider=provider, chroma=chroma)
        # Query back and inspect metadata of the one chunk.
        hits = chroma.query(tenant, query_embedding=[0.1] * 8, k=1)
        assert hits
        meta = hits[0].metadata
        assert meta["doc_id"] == str(kb_doc.id)
        assert meta["source_uri"] == kb_doc.source_uri
        assert meta["doc_type"] == "faq"
        assert meta["tenant_id"] == str(kb_doc.tenant_id)


class TestSkipUnchanged:
    def test_second_ingest_skips_when_checksum_unchanged(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        first = ingest_document(kb_doc, provider=provider, chroma=chroma)
        # First pass embeds.
        assert first.ingested is True
        embed_count_first = len(provider.embedding_calls)

        # Refresh — embedded_at + updated_at written.
        kb_doc.refresh_from_db()

        second = ingest_document(kb_doc, provider=provider, chroma=chroma)
        # Skipped — no new embedding calls.
        assert second.ingested is False
        assert len(provider.embedding_calls) == embed_count_first

    def test_force_flag_bypasses_skip(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        ingest_document(kb_doc, provider=provider, chroma=chroma)
        kb_doc.refresh_from_db()
        before = len(provider.embedding_calls)
        ingest_document(kb_doc, provider=provider, chroma=chroma, force=True)
        # Force re-embeds — more calls than the skip baseline.
        assert len(provider.embedding_calls) > before


class TestContentChange:
    def test_content_edit_triggers_re_embed(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
        tenant: Tenant,
    ) -> None:
        ingest_document(kb_doc, provider=provider, chroma=chroma)
        baseline_count = chroma.collection_count(tenant)

        # Simulate upstream-content edit. Bump checksum to mimic K8 path:
        # the catalog sync writes new content + new checksum + (Django
        # auto_now) new updated_at; embedded_at stays at the old value.
        kb_doc.content = "Часы работы изменились: 09:00-23:00."
        kb_doc.checksum = ""  # force recompute on save
        # Force updated_at to advance past the embedded_at marker.
        kb_doc.embedded_at = datetime.now(timezone.utc) - timedelta(hours=1)
        kb_doc.save()

        ingest_document(kb_doc, provider=provider, chroma=chroma)
        # Stale chunks dropped, new chunks landed — count unchanged for
        # single-chunk FAQ but the doc_id metadata is now the new content.
        hits = chroma.query(tenant, query_embedding=[0.1] * 8, k=10)
        assert all(h.metadata["doc_id"] == str(kb_doc.id) for h in hits)
        # Sanity: chunks aren't doubled.
        assert chroma.collection_count(tenant) == baseline_count

    def test_chunks_for_other_docs_untouched(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
        tenant: Tenant,
    ) -> None:
        # Sibling doc — separate ingest.
        other = KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/2",
            content="Адрес: Пенза, ул. Ленина 1.",
        )
        ingest_document(kb_doc, provider=provider, chroma=chroma)
        ingest_document(other, provider=provider, chroma=chroma)
        # Re-ingest kb_doc with new content.
        kb_doc.content = "Новый текст."
        kb_doc.checksum = ""
        kb_doc.embedded_at = datetime.now(timezone.utc) - timedelta(hours=1)
        kb_doc.save()
        ingest_document(kb_doc, provider=provider, chroma=chroma)
        # Other doc's chunks still present.
        hits = chroma.query(tenant, query_embedding=[0.1] * 8, k=10)
        other_hits = [h for h in hits if h.metadata.get("doc_id") == str(other.id)]
        assert other_hits, "sibling doc chunks were collateral-damaged"


class TestEmptyContent:
    def test_empty_content_marks_embedded_without_chunks(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        empty = KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=KbDocType.FAQ,
            source_uri="mysite://faq/empty",
            content="   \n  ",  # whitespace only
        )
        result = ingest_document(empty, provider=provider, chroma=chroma)
        assert result.ingested is False
        assert result.chunk_count == 0
        empty.refresh_from_db()
        assert empty.embedded_at is not None


class TestAuditAndEvents:
    def test_audit_row_written(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        from apps.audit.models import AuditLog

        ingest_document(kb_doc, provider=provider, chroma=chroma)
        rows = AuditLog.all_tenants.filter(
            action=EVENT_KB_DOCUMENT_INGESTED,
            target_id=str(kb_doc.id),
        )
        assert rows.exists()
        row = rows.first()
        assert row is not None
        assert row.payload["chunk_count"] >= 1
        assert row.payload["doc_type"] == "faq"

    def test_event_emitted(
        self,
        kb_doc: KbDocument,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        from apps.events.models import Event

        ingest_document(kb_doc, provider=provider, chroma=chroma)
        rows = Event.objects.filter(event_name=EVENT_KB_DOCUMENT_INGESTED)
        assert rows.exists()
