"""search_knowledge_base tool tests (DRF-588 / Sprint 7 / F1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache

from apps.audit.models import AuditLog
from apps.kb.chromadb_client import ChromaClient, KbItem, reset_client_cache
from apps.skills.faq.tools import (
    EVENT_KB_SEARCH_INVOKED,
    SEARCH_KB_TOOL_SPEC,
    KbToolResult,
    invalidate_kb_search_cache,
    search_knowledge_base,
)
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _isolated_chroma(tmp_path: Path, settings: pytest.FixtureRequest):
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    settings.CHROMA_HTTP_HOST = ""  # type: ignore[attr-defined]
    # The platform's default settings/local.py leaves CACHES at Django's
    # DummyCache default. The F1 tool relies on a real cache for the
    # hit/miss semantics — override per-test to a clean locmem instance.
    reset_client_cache()
    cache.clear()
    yield
    cache.clear()
    reset_client_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="kb-tool", name="KB Tool")


@pytest.fixture
def chroma() -> ChromaClient:
    """Per-test ChromaClient. Bypasses the LRU singleton so tmp_path
    pinning doesn't leak between cases.
    """
    return ChromaClient()


class FakeProvider:
    name = "fake"
    default_embedding_model = "fake-embed-3"

    def __init__(self) -> None:
        self.embedding_calls: list[str] = []

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        self.embedding_calls.append(text)
        return [((ord(text[i % len(text)]) if text else 1) % 100) / 100.0 for i in range(8)]

    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


def _seed(client: ChromaClient, tenant: Tenant) -> None:
    client.upsert(
        tenant,
        [
            KbItem(
                id="f1",
                text="Часы работы: 10-22",
                embedding=[0.1] * 8,
                metadata={
                    "doc_id": "doc-faq-1",
                    "doc_type": "faq",
                    "source_uri": "mysite://faq/1",
                },
            ),
            KbItem(
                id="f2",
                text="Адрес: Пенза",
                embedding=[0.2] * 8,
                metadata={
                    "doc_id": "doc-faq-2",
                    "doc_type": "faq",
                    "source_uri": "mysite://faq/2",
                },
            ),
        ],
    )


class TestToolSpec:
    def test_shape_matches_l1_canonical(self) -> None:
        assert SEARCH_KB_TOOL_SPEC["name"] == "search_knowledge_base"
        params = SEARCH_KB_TOOL_SPEC["parameters"]
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert params["required"] == ["query"]

    def test_k_bounded_in_schema(self) -> None:
        k_schema = SEARCH_KB_TOOL_SPEC["parameters"]["properties"]["k"]
        assert k_schema["minimum"] == 1
        assert k_schema["maximum"] == 5

    def test_doc_types_enum_covers_six(self) -> None:
        enum = SEARCH_KB_TOOL_SPEC["parameters"]["properties"]["doc_types"]["items"]["enum"]
        assert set(enum) == {
            "service",
            "master",
            "contraindication",
            "faq",
            "help_article",
            "legal",
        }


class TestSearch:
    def test_returns_kb_tool_result(self, tenant: Tenant, chroma: ChromaClient) -> None:
        _seed(chroma, tenant)
        provider = FakeProvider()
        result = search_knowledge_base(tenant, "часы работы", provider=provider, chroma=chroma)
        assert isinstance(result, KbToolResult)
        assert len(result.hits) >= 1
        assert result.cache_hit is False

    def test_hits_trimmed_no_internal_metadata(self, tenant: Tenant, chroma: ChromaClient) -> None:
        _seed(chroma, tenant)
        provider = FakeProvider()
        result = search_knowledge_base(tenant, "x", provider=provider, chroma=chroma)
        hit = result.hits[0]
        assert hit.chunk_id
        assert hit.text
        assert hit.doc_type == "faq"
        assert hit.source_uri.startswith("mysite://")


class TestCacheLayer:
    def test_second_call_hits_cache(self, tenant: Tenant, chroma: ChromaClient) -> None:
        _seed(chroma, tenant)
        provider = FakeProvider()
        search_knowledge_base(tenant, "часы работы", provider=provider, chroma=chroma)
        before = len(provider.embedding_calls)
        second = search_knowledge_base(tenant, "часы работы", provider=provider, chroma=chroma)
        assert second.cache_hit is True
        assert len(provider.embedding_calls) == before

    def test_different_doc_types_different_cache(
        self, tenant: Tenant, chroma: ChromaClient
    ) -> None:
        _seed(chroma, tenant)
        provider = FakeProvider()
        search_knowledge_base(tenant, "x", provider=provider, chroma=chroma, doc_types=["faq"])
        search_knowledge_base(tenant, "x", provider=provider, chroma=chroma, doc_types=["service"])
        # Two distinct cache keys → two embedding calls.
        assert len(provider.embedding_calls) == 2

    def test_different_k_different_cache(self, tenant: Tenant, chroma: ChromaClient) -> None:
        _seed(chroma, tenant)
        provider = FakeProvider()
        search_knowledge_base(tenant, "x", provider=provider, chroma=chroma, k=1)
        search_knowledge_base(tenant, "x", provider=provider, chroma=chroma, k=3)
        assert len(provider.embedding_calls) == 2

    def test_invalidate_clears(self, tenant: Tenant, chroma: ChromaClient) -> None:
        _seed(chroma, tenant)
        provider = FakeProvider()
        search_knowledge_base(tenant, "x", provider=provider, chroma=chroma)
        invalidate_kb_search_cache(tenant)
        result = search_knowledge_base(tenant, "x", provider=provider, chroma=chroma)
        assert result.cache_hit is False


class TestAuditTrail:
    def test_audit_row_per_call(self, tenant: Tenant, chroma: ChromaClient) -> None:
        _seed(chroma, tenant)
        provider = FakeProvider()
        search_knowledge_base(tenant, "часы", provider=provider, chroma=chroma)
        rows = AuditLog.all_tenants.filter(action=EVENT_KB_SEARCH_INVOKED)
        assert rows.exists()
        row = rows.first()
        assert row is not None
        assert row.payload["cache_hit"] is False
        # Query hashed — no PII landed in audit.
        assert "часы" not in row.payload.get("query_hash", "")
        assert len(row.payload["query_hash"]) == 16

    def test_cache_hit_flagged_in_audit(self, tenant: Tenant, chroma: ChromaClient) -> None:
        _seed(chroma, tenant)
        provider = FakeProvider()
        search_knowledge_base(tenant, "x", provider=provider, chroma=chroma)
        search_knowledge_base(tenant, "x", provider=provider, chroma=chroma)
        rows = list(AuditLog.all_tenants.filter(action=EVENT_KB_SEARCH_INVOKED))
        # Two audit rows: one cache miss + one cache hit. Order may
        # collapse on sub-microsecond created_at — check the SET rather
        # than rows[-1].
        flags = {row.payload["cache_hit"] for row in rows}
        assert flags == {False, True}, f"expected both cache states, got {flags!r}"


class TestCacheKeyStability:
    def test_same_inputs_same_key(self) -> None:
        from apps.skills.faq.tools import _cache_key

        a = _cache_key("tenant-1", "x", ["faq"], 3)
        b = _cache_key("tenant-1", "x", ["faq"], 3)
        assert a == b

    def test_doc_types_order_independent(self) -> None:
        from apps.skills.faq.tools import _cache_key

        a = _cache_key("t", "q", ["faq", "service"], 3)
        b = _cache_key("t", "q", ["service", "faq"], 3)
        assert a == b

    def test_sha256_prefix(self) -> None:
        from apps.skills.faq.tools import _cache_key

        key = _cache_key("t", "x", None, 1)
        assert key.startswith("kb_search:")
        digest = key.removeprefix("kb_search:")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)
