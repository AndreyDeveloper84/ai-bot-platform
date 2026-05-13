"""ChromaDB cross-tenant isolation (DRF-596 / Sprint 7 / G1).

The Postgres-side leakage scanner (:mod:`tests.integration.
test_cross_tenant_leakage`) covers ORM model FK enforcement. ChromaDB
lives outside the ORM, so its tenant boundary is a separate property
we verify here:

1. **Naming**: every collection is named ``tenant_<uuid_hex>``
   (Decision 8). The hex form drops dashes and fits ChromaDB's
   ``[a-zA-Z0-9_-]`` charset limit (63 chars max; we use 39).
2. **Search isolation**: tenant A's :meth:`ChromaClient.query` NEVER
   returns chunks tenant B wrote. The wrapper resolves the collection
   name from the tenant id BEFORE every read/write, so a misrouted
   call can't accidentally cross over.
3. **Per-tenant collection cleanup** (K11 / DRF-569) drops only the
   target tenant's collection — the sibling tenant's data survives.

Tests use the real ``PersistentClient`` backed by a per-test
``tmp_path``; no real chromadb server required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.kb.chromadb_client import (
    ChromaClient,
    KbItem,
    collection_name_for_tenant,
    reset_client_cache,
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
def tenant_a(db) -> Tenant:
    return Tenant.objects.create(slug="iso-a", name="Iso A")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="iso-b", name="Iso B")


def _vec(seed: int) -> list[float]:
    rng = [(seed * (i + 1)) % 100 / 100.0 for i in range(8)]
    norm = sum(v * v for v in rng) ** 0.5 or 1.0
    return [v / norm for v in rng]


class TestCollectionNaming:
    """Decision 8 — `tenant_<uuid_hex>` lock-in."""

    def test_naming_is_hex_with_prefix(self, tenant_a: Tenant) -> None:
        name = collection_name_for_tenant(tenant_a)
        assert name.startswith("tenant_")
        suffix = name.removeprefix("tenant_")
        # 32-char hex (no dashes); all lower hex digits.
        assert len(suffix) == 32
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_naming_charset_compliance(self, tenant_a: Tenant) -> None:
        # ChromaDB allows [a-zA-Z0-9_-] only; verify no other chars.
        name = collection_name_for_tenant(tenant_a)
        assert all(c.isalnum() or c == "_" for c in name)
        assert len(name) <= 63

    def test_names_distinct_per_tenant(self, tenant_a: Tenant, tenant_b: Tenant) -> None:
        assert collection_name_for_tenant(tenant_a) != collection_name_for_tenant(tenant_b)


class TestSearchIsolation:
    def test_query_never_crosses_tenants(self, tenant_a: Tenant, tenant_b: Tenant) -> None:
        client = ChromaClient()
        client.upsert(
            tenant_a,
            [
                KbItem(
                    id="a1",
                    text="Tenant A secret content",
                    embedding=_vec(1),
                    metadata={"doc_id": "doc-a"},
                ),
                KbItem(
                    id="a2",
                    text="More A content",
                    embedding=_vec(2),
                    metadata={"doc_id": "doc-a"},
                ),
            ],
        )
        client.upsert(
            tenant_b,
            [
                KbItem(
                    id="b1",
                    text="Tenant B different content",
                    embedding=_vec(3),
                    metadata={"doc_id": "doc-b"},
                )
            ],
        )

        # A query for tenant A returns only A's chunks (even though
        # the underlying chromadb storage holds both A and B rows).
        a_hits = client.query(tenant_a, query_embedding=_vec(1), k=10)
        assert {h.id for h in a_hits} == {"a1", "a2"}

        b_hits = client.query(tenant_b, query_embedding=_vec(1), k=10)
        assert {h.id for h in b_hits} == {"b1"}

    def test_count_isolated_per_tenant(self, tenant_a: Tenant, tenant_b: Tenant) -> None:
        client = ChromaClient()
        client.upsert(
            tenant_a,
            [KbItem(id="c", text="x", embedding=_vec(1), metadata={"doc_id": "d"})],
        )
        assert client.collection_count(tenant_a) == 1
        assert client.collection_count(tenant_b) == 0


class TestPerTenantCleanup:
    def test_delete_collection_only_drops_target(self, tenant_a: Tenant, tenant_b: Tenant) -> None:
        client = ChromaClient()
        client.upsert(
            tenant_a,
            [KbItem(id="a", text="x", embedding=_vec(1), metadata={"doc_id": "d"})],
        )
        client.upsert(
            tenant_b,
            [KbItem(id="b", text="x", embedding=_vec(1), metadata={"doc_id": "d"})],
        )

        # Drop only A's collection.
        client.delete_collection(tenant_a)

        # B's collection still alive.
        b_hits = client.query(tenant_b, query_embedding=_vec(1), k=5)
        assert {h.id for h in b_hits} == {"b"}

        # A's collection re-creates empty on next query.
        a_hits = client.query(tenant_a, query_embedding=_vec(1), k=5)
        assert a_hits == []
