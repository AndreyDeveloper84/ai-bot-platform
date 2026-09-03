"""F1 cache lifecycle tests (DRF-600 / Sprint 7 / G4).

Six scenarios covering cache HIT/MISS, key separation, TTL semantics,
and invalidation. Sibling ``test_tools.py`` proves the happy path;
this file isolates the cache layer.

Scenarios:

1. Cold call → MISS → result stored.
2. Same args within TTL → HIT (``cache_hit=True``); no provider call.
3. ``invalidate_kb_search_cache`` wipes the prefix → next call MISS again.
4. Different ``doc_types`` → different key → independent caches.
5. Different ``k`` → different key.
6. Different tenant → different key (cross-tenant isolation).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache

from apps.kb.chromadb_client import ChromaClient, KbItem, reset_client_cache
from apps.skills.faq.tools import (
    KbToolResult,
    invalidate_kb_search_cache,
    search_knowledge_base,
)
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, settings: Any) -> Any:
    settings.BASE_DIR = tmp_path
    settings.CHROMA_HTTP_HOST = ""
    reset_client_cache()
    cache.clear()
    yield
    cache.clear()
    reset_client_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="g4-cache", name="G4 Cache")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="g4-cache-b", name="G4 Cache B")


class _CountingProvider:
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
        self.calls = 0

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        self.calls += 1
        return [0.5] * 8

    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


def _seed(tenant: Tenant, *, chunk_id: str = "c1", doc_type: str = "faq") -> None:
    ChromaClient().upsert(
        tenant,
        [
            KbItem(
                id=chunk_id,
                text=f"chunk-{chunk_id}",
                embedding=[0.5] * 8,
                metadata={
                    "doc_id": chunk_id,
                    "doc_type": doc_type,
                    "source_uri": f"mysite://{doc_type}/{chunk_id}",
                },
            )
        ],
    )


# ---------------------------------------------------------------------------
# 1. Cold MISS
# ---------------------------------------------------------------------------


def test_first_call_is_cache_miss(tenant: Tenant) -> None:
    _seed(tenant)
    provider = _CountingProvider()

    result = search_knowledge_base(tenant, "часы", provider=provider, k=1)

    assert isinstance(result, KbToolResult)
    assert result.cache_hit is False
    assert provider.calls == 1  # one embedding round-trip


# ---------------------------------------------------------------------------
# 2. Repeat HIT
# ---------------------------------------------------------------------------


def test_identical_args_within_ttl_hit(tenant: Tenant) -> None:
    _seed(tenant)
    provider = _CountingProvider()

    first = search_knowledge_base(tenant, "часы", provider=provider, k=1)
    second = search_knowledge_base(tenant, "часы", provider=provider, k=1)

    assert first.cache_hit is False
    assert second.cache_hit is True
    # Second call must NOT have invoked the provider.
    assert provider.calls == 1
    # Hit shapes match.
    assert [h.chunk_id for h in second.hits] == [h.chunk_id for h in first.hits]


# ---------------------------------------------------------------------------
# 3. Invalidation wipes prefix
# ---------------------------------------------------------------------------


def test_invalidation_forces_next_miss(tenant: Tenant) -> None:
    _seed(tenant)
    provider = _CountingProvider()

    search_knowledge_base(tenant, "часы", provider=provider, k=1)
    invalidate_kb_search_cache(tenant)
    third = search_knowledge_base(tenant, "часы", provider=provider, k=1)

    assert third.cache_hit is False
    # The invalidator forced a fresh embedding call.
    assert provider.calls == 2


# ---------------------------------------------------------------------------
# 4. Different doc_types → different key
# ---------------------------------------------------------------------------


def test_different_doc_types_different_cache_key(tenant: Tenant) -> None:
    _seed(tenant, doc_type="faq")
    _seed(tenant, chunk_id="c2", doc_type="help_article")
    provider = _CountingProvider()

    a = search_knowledge_base(tenant, "часы", provider=provider, doc_types=["faq"], k=2)
    b = search_knowledge_base(tenant, "часы", provider=provider, doc_types=["help_article"], k=2)

    assert a.cache_hit is False
    assert b.cache_hit is False, (
        "different doc_types should produce different cache keys, "
        "but the second call hit the first call's cache"
    )
    # Two distinct provider invocations (one per doc_type).
    assert provider.calls == 2


# ---------------------------------------------------------------------------
# 5. Different k → different key
# ---------------------------------------------------------------------------


def test_different_k_different_cache_key(tenant: Tenant) -> None:
    _seed(tenant)
    provider = _CountingProvider()

    search_knowledge_base(tenant, "часы", provider=provider, k=1)
    second = search_knowledge_base(tenant, "часы", provider=provider, k=3)

    assert second.cache_hit is False
    assert provider.calls == 2


# ---------------------------------------------------------------------------
# 6. Different tenant → cross-tenant cache isolation
# ---------------------------------------------------------------------------


def test_different_tenant_does_not_share_cache(tenant: Tenant, tenant_b: Tenant) -> None:
    _seed(tenant)
    _seed(tenant_b, chunk_id="bx", doc_type="faq")
    provider = _CountingProvider()

    a = search_knowledge_base(tenant, "часы", provider=provider, k=1)
    b = search_knowledge_base(tenant_b, "часы", provider=provider, k=1)

    assert a.cache_hit is False
    assert b.cache_hit is False, "cross-tenant cache leak: tenant_b hit tenant_a's cache entry"
    assert provider.calls == 2
    # Hits MUST come from each tenant's own collection.
    assert {h.chunk_id for h in a.hits} == {"c1"}
    assert {h.chunk_id for h in b.hits} == {"bx"}
