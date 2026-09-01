"""KB retriever global-fallback tests (KB-RAG Sub-3 / GH #116).

Covers the cross-tenant retrieval fallback for shared doc_types
(``SERVICE`` / ``CONTRAINDICATION`` / ``HELP_ARTICLE``) and the strict
per-tenant isolation for the three sensitive doc_types
(``MASTER`` / ``FAQ`` / ``LEGAL``).

The fallback queries the ``global_kb`` system tenant's collection
in addition to the calling tenant's collection and merges by score.
The security invariant is hard-asserted: for ``MASTER`` / ``FAQ`` /
``LEGAL`` the global collection is **not** queried under any
circumstance — cross-tenant leakage there would be a tenancy breach.

These tests mirror the style of :mod:`test_retriever`:

* per-test ``tmp_path`` BASE_DIR + ``reset_client_cache``;
* hand-rolled :class:`FakeProvider` over the real ``LLMProvider``
  protocol — no real OpenAI calls;
* real :class:`ChromaClient` against the temporary persist root.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from apps.kb.chromadb_client import ChromaClient, KbItem, reset_client_cache
from apps.kb.constants import GLOBAL_KB_TENANT_SLUG
from apps.kb.services.global_tenant import get_global_kb_tenant
from apps.kb.services.retriever import search_kb
from apps.tenancy.models import Tenant

# Re-exported here as the legacy test-only alias. New callers should
# import from ``apps.kb.constants`` directly (Sub-2 / GH #115).
GLOBAL_KB_SLUG = GLOBAL_KB_TENANT_SLUG


@pytest.fixture(autouse=True)
def _isolated_chroma(tmp_path: Path, settings: pytest.FixtureRequest) -> Any:
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    settings.CHROMA_HTTP_HOST = ""  # type: ignore[attr-defined]
    reset_client_cache()
    # Clear the shared global-tenant cache so each test starts fresh.
    # Sub-2 (GH #115) extracted this lookup from the retriever into a
    # public helper so seed cmd (Sub-5) can reuse it; the cache lives on
    # the helper now, not on a retriever-private symbol.
    get_global_kb_tenant.cache_clear()
    yield
    reset_client_cache()
    get_global_kb_tenant.cache_clear()


@pytest.fixture
def tenant(db: Any) -> Tenant:
    return Tenant.objects.create(slug="kb-retr-gf", name="KB Retr GF")


@pytest.fixture
def global_tenant(db: Any) -> Tenant:
    return Tenant.objects.create(slug=GLOBAL_KB_SLUG, name="Global KB")


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


def _seed(
    chroma: ChromaClient,
    tenant: Tenant,
    doc_type: str,
    n: int = 1,
    *,
    id_prefix: str | None = None,
) -> None:
    """Push ``n`` simple chunks into chromadb under ``doc_type``."""
    prefix = id_prefix if id_prefix is not None else doc_type
    items = [
        KbItem(
            id=f"{prefix}-{i}",
            text=f"{prefix} chunk {i}",
            embedding=[((i + 1) * 0.1) % 1.0] * 8,
            metadata={
                "doc_id": f"doc-{prefix}-{i}",
                "doc_type": doc_type,
            },
        )
        for i in range(n)
    ]
    chroma.upsert(tenant, items)


# ---------------------------------------------------------------------------
# Recording wrapper — lets us assert *which* tenants were queried.
# ---------------------------------------------------------------------------


class _RecordingChromaClient:
    """Wraps a real ChromaClient, records every ``query`` invocation.

    We use composition (not subclass) so we capture the tenant argument
    *exactly* as the retriever passes it, without any short-circuit
    inside the wrapper.
    """

    def __init__(self, inner: ChromaClient) -> None:
        self._inner = inner
        self.queried_tenants: list[Any] = []

    def collection_count(self, tenant: Any) -> int:
        return self._inner.collection_count(tenant)

    def query(
        self,
        tenant: Any,
        query_embedding: list[float],
        *,
        k: int = 3,
        where: dict[str, Any] | None = None,
    ) -> Any:
        self.queried_tenants.append(tenant)
        return self._inner.query(tenant, query_embedding, k=k, where=where)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSharedDocTypesQueryBothCollections:
    """SERVICE / CONTRAINDICATION / HELP_ARTICLE → tenant + global."""

    def test_service_query_merges_tenant_and_global(
        self,
        tenant: Tenant,
        global_tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        _seed(chroma, tenant, "service", n=2, id_prefix="t-service")
        _seed(chroma, global_tenant, "service", n=2, id_prefix="g-service")

        recorder = _RecordingChromaClient(chroma)
        result = search_kb(
            tenant,
            "услуга",
            provider=provider,
            chroma=recorder,  # type: ignore[arg-type]
            k=5,
            doc_types=["service"],
        )
        # Both tenant and global_kb collections were queried.
        queried_ids = {getattr(t, "id", t) for t in recorder.queried_tenants}
        assert tenant.id in queried_ids
        assert global_tenant.id in queried_ids
        # Results from both surface in the merged list.
        sources = {h.metadata.get("kb_source") for h in result.hits}
        assert "tenant" in sources
        assert "global" in sources

    def test_contraindication_query_uses_global_fallback(
        self,
        tenant: Tenant,
        global_tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        _seed(chroma, tenant, "contraindication", n=1, id_prefix="t-contra")
        _seed(chroma, global_tenant, "contraindication", n=1, id_prefix="g-contra")

        recorder = _RecordingChromaClient(chroma)
        search_kb(
            tenant,
            "противопоказания",
            provider=provider,
            chroma=recorder,  # type: ignore[arg-type]
            k=3,
            doc_types=["contraindication"],
        )
        queried_ids = {getattr(t, "id", t) for t in recorder.queried_tenants}
        assert global_tenant.id in queried_ids

    def test_help_article_query_uses_global_fallback(
        self,
        tenant: Tenant,
        global_tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        _seed(chroma, tenant, "help_article", n=1, id_prefix="t-help")
        _seed(chroma, global_tenant, "help_article", n=1, id_prefix="g-help")

        recorder = _RecordingChromaClient(chroma)
        search_kb(
            tenant,
            "помощь",
            provider=provider,
            chroma=recorder,  # type: ignore[arg-type]
            k=3,
            doc_types=["help_article"],
        )
        queried_ids = {getattr(t, "id", t) for t in recorder.queried_tenants}
        assert global_tenant.id in queried_ids


class TestSensitiveDocTypesNeverHitGlobal:
    """SECURITY — MASTER / FAQ / LEGAL must be strictly per-tenant.

    These assertions are hard: the global tenant id must never appear in
    the recorded query list, no matter how the merge happens.
    """

    def test_master_query_does_not_hit_global(
        self,
        tenant: Tenant,
        global_tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        _seed(chroma, tenant, "master", n=1, id_prefix="t-master")
        # Plant something in global to make sure leakage would be detectable.
        _seed(chroma, global_tenant, "master", n=1, id_prefix="g-master")

        recorder = _RecordingChromaClient(chroma)
        result = search_kb(
            tenant,
            "мастер",
            provider=provider,
            chroma=recorder,  # type: ignore[arg-type]
            k=3,
            doc_types=["master"],
        )
        queried_ids = {getattr(t, "id", t) for t in recorder.queried_tenants}
        assert global_tenant.id not in queried_ids
        # And no global-sourced hit slipped through.
        assert all(h.metadata.get("kb_source") != "global" for h in result.hits)

    def test_faq_query_does_not_hit_global(
        self,
        tenant: Tenant,
        global_tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        _seed(chroma, tenant, "faq", n=1, id_prefix="t-faq")
        _seed(chroma, global_tenant, "faq", n=1, id_prefix="g-faq")

        recorder = _RecordingChromaClient(chroma)
        result = search_kb(
            tenant,
            "вопрос",
            provider=provider,
            chroma=recorder,  # type: ignore[arg-type]
            k=3,
            doc_types=["faq"],
        )
        queried_ids = {getattr(t, "id", t) for t in recorder.queried_tenants}
        assert global_tenant.id not in queried_ids
        assert all(h.metadata.get("kb_source") != "global" for h in result.hits)

    def test_legal_query_does_not_hit_global(
        self,
        tenant: Tenant,
        global_tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        _seed(chroma, tenant, "legal", n=1, id_prefix="t-legal")
        _seed(chroma, global_tenant, "legal", n=1, id_prefix="g-legal")

        recorder = _RecordingChromaClient(chroma)
        result = search_kb(
            tenant,
            "юридический",
            provider=provider,
            chroma=recorder,  # type: ignore[arg-type]
            k=3,
            doc_types=["legal"],
        )
        queried_ids = {getattr(t, "id", t) for t in recorder.queried_tenants}
        assert global_tenant.id not in queried_ids
        assert all(h.metadata.get("kb_source") != "global" for h in result.hits)


class TestMergeBehaviour:
    def test_merge_respects_top_k(
        self,
        tenant: Tenant,
        global_tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        # Seed plenty in both collections so combined candidates > K.
        _seed(chroma, tenant, "service", n=5, id_prefix="t-service")
        _seed(chroma, global_tenant, "service", n=5, id_prefix="g-service")

        result = search_kb(
            tenant,
            "услуга",
            provider=provider,
            chroma=chroma,
            k=5,
            doc_types=["service"],
        )
        assert len(result.hits) <= 5
        # Scores monotonically descending after merge.
        scores = [h.score for h in result.hits]
        assert scores == sorted(scores, reverse=True)

    def test_kb_source_metadata_set(
        self,
        tenant: Tenant,
        global_tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
    ) -> None:
        _seed(chroma, tenant, "service", n=2, id_prefix="t-service")
        _seed(chroma, global_tenant, "service", n=2, id_prefix="g-service")

        result = search_kb(
            tenant,
            "услуга",
            provider=provider,
            chroma=chroma,
            k=5,
            doc_types=["service"],
        )
        # Every hit must carry the provenance marker.
        for hit in result.hits:
            assert hit.metadata.get("kb_source") in {"tenant", "global"}
        sources = {h.metadata["kb_source"] for h in result.hits}
        # Both provenances appear when both collections have content.
        assert sources == {"tenant", "global"}


class TestGracefulDegradation:
    def test_global_kb_missing_does_not_break_retrieval(
        self,
        tenant: Tenant,
        provider: FakeProvider,
        chroma: ChromaClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # No global_tenant fixture — Tenant.all_objects.filter(slug="global_kb")
        # returns None. Sanity-check that precondition.
        assert Tenant.all_objects.filter(slug=GLOBAL_KB_SLUG).first() is None

        _seed(chroma, tenant, "service", n=2, id_prefix="t-service")

        # The WARN now lives in ``apps.kb.services.global_tenant`` (Sub-2
        # extracted the lookup into the shared helper) — the test pins
        # the new log surface so future regressions surface here.
        with caplog.at_level(logging.WARNING, logger="apps.kb.services.global_tenant"):
            result = search_kb(
                tenant,
                "услуга",
                provider=provider,
                chroma=chroma,
                k=5,
                doc_types=["service"],
            )
        # Tenant-only results returned, no exception.
        assert len(result.hits) >= 1
        assert all(h.metadata.get("kb_source") == "tenant" for h in result.hits)
        # Exactly one WARN about the missing global tenant.
        warns = [r for r in caplog.records if "global_tenant.missing" in r.getMessage()]
        assert len(warns) == 1
