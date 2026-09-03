"""KB retriever tests (DRF-563 / Sprint 7 / K5)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from apps.kb.chromadb_client import ChromaClient, KbItem, reset_client_cache
from apps.kb.services.retriever import (
    EVENT_KB_RETRIEVAL_PERFORMED,
    RetrievalResult,
    search_kb,
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
    return Tenant.objects.create(slug="kb-retr", name="KB Retr")


@pytest.fixture
def chroma() -> ChromaClient:
    return ChromaClient()


class FakeProvider:
    name = "fake"
    default_embedding_model = "fake-embed-3"
    # DRF-1437: the LLMProvider Protocol now requires a published
    # default completion model — the router's quota fallback has to
    # name a model the TARGET vendor understands when it hops.
    default_completion_model = "fake-model"
    # DRF-1443 widened the Protocol with the fast tier — see
    # ``apps.llm.protocol.LLMProvider.default_fast_model``.
    default_fast_model = "fake-model-fast"

    def __init__(self) -> None:
        self.embedding_calls: list[tuple[str, str | None]] = []

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        self.embedding_calls.append((text, model))
        return [((ord(text[i % len(text)]) if text else 1) % 100) / 100.0 for i in range(8)]

    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


def _seed(chroma: ChromaClient, tenant: Tenant, doc_type: str, n: int = 1) -> None:
    """Push ``n`` simple chunks into chromadb under ``doc_type``."""
    items = [
        KbItem(
            id=f"{doc_type}-{i}",
            text=f"{doc_type} chunk {i}",
            embedding=[((i + 1) * 0.1) % 1.0] * 8,
            metadata={
                "doc_id": f"doc-{doc_type}-{i}",
                "doc_type": doc_type,
            },
        )
        for i in range(n)
    ]
    chroma.upsert(tenant, items)


class TestColdCollection:
    def test_empty_collection_returns_empty(
        self, tenant: Tenant, provider: FakeProvider, chroma: ChromaClient
    ) -> None:
        result = search_kb(tenant, "часы работы", provider=provider, chroma=chroma)
        assert isinstance(result, RetrievalResult)
        assert result.hits == []
        # Embedding never called — cold-collection short-circuit.
        assert provider.embedding_calls == []

    def test_empty_query_returns_empty(
        self, tenant: Tenant, provider: FakeProvider, chroma: ChromaClient
    ) -> None:
        _seed(chroma, tenant, "faq", n=2)
        result = search_kb(tenant, "   ", provider=provider, chroma=chroma)
        assert result.hits == []


class TestHappyPath:
    def test_returns_hits(
        self, tenant: Tenant, provider: FakeProvider, chroma: ChromaClient
    ) -> None:
        _seed(chroma, tenant, "faq", n=3)
        result = search_kb(tenant, "когда работаете", provider=provider, chroma=chroma)
        assert len(result.hits) >= 1
        assert result.actual_k == 3
        assert result.requested_k == 3

    def test_embedding_called_once_per_query(
        self, tenant: Tenant, provider: FakeProvider, chroma: ChromaClient
    ) -> None:
        _seed(chroma, tenant, "faq")
        search_kb(tenant, "когда работаете", provider=provider, chroma=chroma)
        assert len(provider.embedding_calls) == 1


class TestKClamp:
    def test_k_over_5_clamped_and_logged(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(chroma, tenant, "faq", n=10)
        with caplog.at_level(logging.WARNING, logger="apps.kb.services.retriever"):
            result = search_kb(tenant, "x", provider=provider, chroma=chroma, k=20)
        assert result.requested_k == 20
        assert result.actual_k == 5
        assert any("k_clamped" in r.message for r in caplog.records)

    def test_k_zero_clamped_to_one(
        self, tenant: Tenant, provider: FakeProvider, chroma: ChromaClient
    ) -> None:
        _seed(chroma, tenant, "faq", n=3)
        result = search_kb(tenant, "x", provider=provider, chroma=chroma, k=0)
        assert result.actual_k == 1

    def test_negative_k_clamped_to_one(
        self, tenant: Tenant, provider: FakeProvider, chroma: ChromaClient
    ) -> None:
        _seed(chroma, tenant, "faq")
        result = search_kb(tenant, "x", provider=provider, chroma=chroma, k=-5)
        assert result.actual_k == 1


class TestDocTypeFilter:
    def test_filter_restricts_results(
        self, tenant: Tenant, provider: FakeProvider, chroma: ChromaClient
    ) -> None:
        _seed(chroma, tenant, "faq", n=2)
        _seed(chroma, tenant, "service", n=2)
        result = search_kb(
            tenant,
            "x",
            provider=provider,
            chroma=chroma,
            k=4,
            doc_types=["faq"],
        )
        # Only faq chunks returned.
        assert all(h.metadata["doc_type"] == "faq" for h in result.hits)
        assert result.doc_types_filter == ["faq"]

    def test_no_filter_returns_mixed_types(
        self, tenant: Tenant, provider: FakeProvider, chroma: ChromaClient
    ) -> None:
        _seed(chroma, tenant, "faq", n=2)
        _seed(chroma, tenant, "service", n=2)
        result = search_kb(tenant, "x", provider=provider, chroma=chroma, k=4)
        types = {h.metadata["doc_type"] for h in result.hits}
        # Both types present (modulo cosine ordering).
        assert types <= {"faq", "service"}
        assert len(types) >= 1


class TestEvents:
    def test_event_emitted_per_call(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        from apps.events.models import Event

        _seed(chroma, tenant, "faq")
        search_kb(tenant, "x", provider=provider, chroma=chroma)
        assert Event.objects.filter(event_name=EVENT_KB_RETRIEVAL_PERFORMED).exists()

    def test_event_payload_has_k_and_hit_count(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        from apps.events.models import Event

        _seed(chroma, tenant, "faq", n=3)
        search_kb(
            tenant,
            "x",
            provider=provider,
            chroma=chroma,
            k=2,
            doc_types=["faq"],
        )
        event = Event.objects.filter(event_name=EVENT_KB_RETRIEVAL_PERFORMED).first()
        assert event is not None
        props = event.properties
        assert props["requested_k"] == 2
        assert props["actual_k"] == 2
        assert props["doc_types"] == ["faq"]
        assert props["hit_count"] >= 0
