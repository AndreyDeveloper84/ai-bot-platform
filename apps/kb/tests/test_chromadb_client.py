"""ChromaDB client tests (DRF-560 / Sprint 7 / K2).

These tests exercise the wrapper against a real :class:`PersistentClient`
backed by `tmp_path/.chroma`. We don't mock chromadb — the wrapper is a
thin façade and the interesting behaviour (collection naming, cosine
score rescale, delete idempotency) only makes sense to test end-to-end.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from apps.kb.chromadb_client import (
    ChromaClient,
    KbHit,
    KbItem,
    collection_name_for_tenant,
    reset_client_cache,
)
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _isolated_chroma(tmp_path: Path, settings: pytest.FixtureRequest) -> Iterator[None]:
    """Point chromadb at a fresh per-test directory + reset the LRU cache."""
    # Override BASE_DIR so the chromadb persist root is per-test.
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    # Force HTTP host empty so the client picks PersistentClient.
    settings.CHROMA_HTTP_HOST = ""  # type: ignore[attr-defined]
    reset_client_cache()
    yield
    reset_client_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="kb-chroma-a", name="A")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="kb-chroma-b", name="B")


def _vec(seed: int, dim: int = 16) -> list[float]:
    """Deterministic small embedding for upsert/query tests."""
    rng = [(seed * (i + 1)) % 100 / 100.0 for i in range(dim)]
    # Normalise so cosine math is meaningful.
    norm = sum(v * v for v in rng) ** 0.5 or 1.0
    return [v / norm for v in rng]


class TestCollectionNaming:
    def test_decision_8_naming_from_tenant(self, tenant: Tenant) -> None:
        name = collection_name_for_tenant(tenant)
        assert name == f"tenant_{tenant.id.hex}"
        # 32-char hex + "tenant_" prefix = 39 chars, well under chromadb's 63 cap.
        assert len(name) == 39

    def test_naming_from_uuid_directly(self) -> None:
        u = uuid.uuid4()
        assert collection_name_for_tenant(u) == f"tenant_{u.hex}"

    def test_naming_from_stringified_uuid(self) -> None:
        u = uuid.uuid4()
        assert collection_name_for_tenant(str(u)) == f"tenant_{u.hex}"

    def test_charset_compliance(self, tenant: Tenant) -> None:
        name = collection_name_for_tenant(tenant)
        # chromadb collection names must match [a-zA-Z0-9_-].
        assert all(c.isalnum() or c == "_" for c in name)


class TestUpsertAndQuery:
    def test_upsert_then_query_returns_hit(self, tenant: Tenant) -> None:
        client = ChromaClient()
        client.upsert(
            tenant,
            [
                KbItem(
                    id="c1",
                    text="Часы работы 10-22",
                    embedding=_vec(1),
                    metadata={"doc_id": "doc-1", "doc_type": "faq"},
                )
            ],
        )
        hits = client.query(tenant, query_embedding=_vec(1), k=1)
        assert len(hits) == 1
        assert hits[0].id == "c1"
        assert isinstance(hits[0], KbHit)

    def test_query_empty_collection_returns_empty(self, tenant: Tenant) -> None:
        client = ChromaClient()
        assert client.query(tenant, query_embedding=_vec(1), k=3) == []

    def test_query_k_zero_returns_empty(self, tenant: Tenant) -> None:
        client = ChromaClient()
        client.upsert(
            tenant,
            [KbItem(id="c1", text="x", embedding=_vec(1), metadata={"doc_id": "d"})],
        )
        assert client.query(tenant, query_embedding=_vec(1), k=0) == []

    def test_upsert_empty_list_noop(self, tenant: Tenant) -> None:
        client = ChromaClient()
        client.upsert(tenant, [])  # should not raise
        assert client.collection_count(tenant) == 0

    def test_upsert_idempotent_on_same_id(self, tenant: Tenant) -> None:
        client = ChromaClient()
        for text in ("v1", "v2"):
            client.upsert(
                tenant,
                [
                    KbItem(
                        id="c1",
                        text=text,
                        embedding=_vec(1),
                        metadata={"doc_id": "d", "version": text},
                    )
                ],
            )
        # Second upsert overwrites — exactly one row remains.
        assert client.collection_count(tenant) == 1


class TestCrossTenantIsolation:
    """G1 (DRF-596) will run a stricter scanner; this is the unit guard."""

    def test_tenant_a_query_never_sees_tenant_b_chunks(
        self, tenant: Tenant, tenant_b: Tenant
    ) -> None:
        client = ChromaClient()
        client.upsert(
            tenant,
            [
                KbItem(
                    id="a-1",
                    text="A's chunk",
                    embedding=_vec(1),
                    metadata={"doc_id": "a-doc"},
                )
            ],
        )
        client.upsert(
            tenant_b,
            [
                KbItem(
                    id="b-1",
                    text="B's chunk",
                    embedding=_vec(1),
                    metadata={"doc_id": "b-doc"},
                )
            ],
        )
        a_hits = client.query(tenant, query_embedding=_vec(1), k=10)
        b_hits = client.query(tenant_b, query_embedding=_vec(1), k=10)
        assert {h.id for h in a_hits} == {"a-1"}
        assert {h.id for h in b_hits} == {"b-1"}

    def test_collection_names_distinct(self, tenant: Tenant, tenant_b: Tenant) -> None:
        assert collection_name_for_tenant(tenant) != collection_name_for_tenant(tenant_b)


class TestDeleteSemantics:
    def test_delete_document_drops_only_matching_doc_id(self, tenant: Tenant) -> None:
        client = ChromaClient()
        client.upsert(
            tenant,
            [
                KbItem(id="c1", text="x", embedding=_vec(1), metadata={"doc_id": "A"}),
                KbItem(id="c2", text="y", embedding=_vec(2), metadata={"doc_id": "B"}),
            ],
        )
        client.delete_document(tenant, doc_id="A")
        remaining_hits = client.query(tenant, query_embedding=_vec(2), k=5)
        assert {h.id for h in remaining_hits} == {"c2"}

    def test_delete_collection_idempotent(self, tenant: Tenant) -> None:
        client = ChromaClient()
        # First call — no collection ever created; must not raise.
        client.delete_collection(tenant)
        # Create some content + drop again.
        client.upsert(
            tenant,
            [KbItem(id="c1", text="x", embedding=_vec(1), metadata={"doc_id": "A"})],
        )
        client.delete_collection(tenant)
        # And once more on the now-absent collection — still safe.
        client.delete_collection(tenant)


class TestScoreRescale:
    def test_score_in_zero_to_one_range(self, tenant: Tenant) -> None:
        client = ChromaClient()
        client.upsert(
            tenant,
            [KbItem(id="c1", text="x", embedding=_vec(1), metadata={"doc_id": "d"})],
        )
        hits = client.query(tenant, query_embedding=_vec(1), k=1)
        assert 0.0 <= hits[0].score <= 1.0
        # Identical vectors should score near 1.0.
        assert hits[0].score > 0.9


class TestCollectionCount:
    def test_count_grows_with_upserts(self, tenant: Tenant) -> None:
        client = ChromaClient()
        assert client.collection_count(tenant) == 0
        client.upsert(
            tenant,
            [
                KbItem(id="c1", text="x", embedding=_vec(1), metadata={"doc_id": "A"}),
                KbItem(id="c2", text="y", embedding=_vec(2), metadata={"doc_id": "B"}),
            ],
        )
        assert client.collection_count(tenant) == 2


class TestHttpClientAuth:
    """Sprint 7 / M4 (DRF-595) — HttpClient threads ``CHROMA_AUTH_TOKEN`` into
    the ``chromadb.config.Settings(chroma_client_auth_*)`` payload so requests
    carry the Bearer header chromadb expects under
    ``token_authn.TokenAuthClientProvider``.

    Mocks ``chromadb.HttpClient`` to capture the constructor kwargs — the
    real network call is irrelevant; we only assert wiring.
    """

    def test_token_threaded_into_client_settings(
        self,
        settings: pytest.FixtureRequest,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import chromadb

        from apps.kb import chromadb_client as cc

        captured: dict[str, object] = {}

        def fake_http_client(*, host: str, port: int, **kwargs: object) -> object:
            captured["host"] = host
            captured["port"] = port
            captured["settings"] = kwargs.get("settings")
            return object()  # opaque — the wrapper never touches the real client here

        monkeypatch.setattr(chromadb, "HttpClient", fake_http_client)
        settings.CHROMA_HTTP_HOST = "chroma.internal"  # type: ignore[attr-defined]
        settings.CHROMA_HTTP_PORT = 8001  # type: ignore[attr-defined]
        settings.CHROMA_AUTH_TOKEN = "test-bearer-token-32chars-xxxxxxx"  # type: ignore[attr-defined]
        cc.reset_client_cache()

        cc._build_chromadb_client()

        assert captured["host"] == "chroma.internal"
        assert captured["port"] == 8001
        chroma_settings = captured["settings"]
        # `Settings` is a pydantic model; expose values via attribute access.
        assert getattr(chroma_settings, "chroma_client_auth_provider", "") == (
            "chromadb.auth.token_authn.TokenAuthClientProvider"
        )
        assert getattr(chroma_settings, "chroma_client_auth_credentials", "") == (
            "test-bearer-token-32chars-xxxxxxx"
        )

    def test_empty_token_skips_auth_settings(
        self,
        settings: pytest.FixtureRequest,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No token → HttpClient invoked without ``settings`` kwarg
        (local dev / staging-without-auth safety net).
        """
        import chromadb

        from apps.kb import chromadb_client as cc

        captured_kwargs: dict[str, object] = {}

        def fake_http_client(**kwargs: object) -> object:
            captured_kwargs.update(kwargs)
            return object()

        monkeypatch.setattr(chromadb, "HttpClient", fake_http_client)
        settings.CHROMA_HTTP_HOST = "chroma.internal"  # type: ignore[attr-defined]
        settings.CHROMA_AUTH_TOKEN = ""  # type: ignore[attr-defined]
        cc.reset_client_cache()

        cc._build_chromadb_client()

        assert "settings" not in captured_kwargs, (
            "empty CHROMA_AUTH_TOKEN must not produce a chromadb auth-settings "
            f"payload; got kwargs={captured_kwargs!r}"
        )
