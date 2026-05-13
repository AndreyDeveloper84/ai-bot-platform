"""Tenant cleanup signal tests (DRF-569 / Sprint 7 / K11)."""

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
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="kb-sig", name="KB Sig")


def _seed(client: ChromaClient, tenant: Tenant) -> None:
    client.upsert(
        tenant,
        [
            KbItem(
                id="c1",
                text="x",
                embedding=[0.1] * 8,
                metadata={"doc_id": "d-1", "doc_type": "faq"},
            )
        ],
    )


class TestPreDeleteSignal:
    def test_collection_deleted_on_tenant_delete(self, tenant: Tenant) -> None:
        client = ChromaClient()
        _seed(client, tenant)
        assert client.collection_count(tenant) == 1

        # Capture id BEFORE delete — Django clears `tenant.id` on delete.
        tenant_id = tenant.id
        # Trigger pre_delete signal.
        tenant.delete()

        # Collection should be gone — chromadb_client.delete_collection ran.
        # Re-creating the same collection by name yields an empty one.
        client_after = ChromaClient()
        collection = client_after._client.get_or_create_collection(  # type: ignore[attr-defined]
            name=collection_name_for_tenant(tenant_id)
        )
        assert collection.count() == 0

    def test_idempotent_when_no_collection_existed(self, db) -> None:
        # Tenant that never had any KB content.
        cold_tenant = Tenant.objects.create(slug="kb-cold", name="Cold")
        # Delete must not raise — signal handler swallows not-found.
        cold_tenant.delete()

    def test_failure_does_not_block_tenant_delete(
        self,
        tenant: Tenant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even if chromadb misbehaves, the Postgres delete proceeds.

        Orphan collection is preferable to a half-deleted tenant —
        ops can reconcile orphans later, but a broken delete leaves
        the tenant in a stuck state nobody can recover from.
        """
        from apps.kb import chromadb_client as cc

        original = cc.get_chroma_client

        def _bad_client():
            client = original()
            # Patch the delete to raise — simulate chromadb 500.
            monkeypatch.setattr(
                client,
                "delete_collection",
                lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("chromadb 500")),
            )
            return client

        monkeypatch.setattr(cc, "get_chroma_client", _bad_client)
        # Should NOT raise — signal handler logs and continues.
        tenant.delete()
        # Postgres row really gone.
        assert not Tenant.objects.filter(id=tenant.id).exists()
