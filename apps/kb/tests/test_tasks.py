"""KB Celery task tests (DRF-564 / Sprint 7 / K6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from apps.kb.chromadb_client import reset_client_cache
from apps.kb.models import KbDocType, KbDocument
from apps.kb.tasks import (
    _ingest_queryset,
    embed_pending_kb_documents,
    reindex_tenant_kb,
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
    return Tenant.objects.create(slug="kb-task", name="KB Task")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="kb-task-b", name="KB Task B")


class FakeProvider:
    name = "fake"
    default_embedding_model = "fake-embed-3"

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.embedding_calls: list[str] = []

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        self.embedding_calls.append(text)
        if self.fail_on is not None and self.fail_on in text:
            from apps.llm.protocol import LLMError

            raise LLMError(f"synthetic failure on {self.fail_on!r}")
        return [((ord(text[i % len(text)]) if text else 1) % 100) / 100.0 for i in range(8)]

    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


def _make_doc(tenant: Tenant, *, suffix: str = "1", doc_type: str = KbDocType.FAQ) -> KbDocument:
    return KbDocument.all_tenants.create(
        tenant=tenant,
        doc_type=doc_type,
        source_uri=f"mysite://faq/{suffix}",
        content=f"FAQ body {suffix}",
    )


class TestReindexTenantKb:
    def test_walks_all_docs(self, tenant: Tenant) -> None:
        for s in ("1", "2", "3"):
            _make_doc(tenant, suffix=s)
        provider = FakeProvider()
        counters = _ingest_queryset(
            KbDocument.all_tenants.filter(tenant=tenant),
            force=True,
            label="test_reindex",
            provider=provider,
        )
        assert counters["processed"] == 3
        assert counters["ingested"] == 3
        assert counters["failed"] == 0

    def test_per_doc_failure_isolated(self, tenant: Tenant) -> None:
        _make_doc(tenant, suffix="ok-1")
        _make_doc(tenant, suffix="boom")
        _make_doc(tenant, suffix="ok-2")
        # FakeProvider raises LLMError when text contains "boom".
        provider = FakeProvider(fail_on="boom")
        counters = _ingest_queryset(
            KbDocument.all_tenants.filter(tenant=tenant),
            force=True,
            label="test_isolation",
            provider=provider,
        )
        assert counters["processed"] == 3
        assert counters["ingested"] == 2
        assert counters["failed"] == 1

    def test_doc_type_filter(self, tenant: Tenant) -> None:
        _make_doc(tenant, suffix="1", doc_type=KbDocType.FAQ)
        _make_doc(tenant, suffix="2", doc_type=KbDocType.SERVICE)
        provider = FakeProvider()
        counters = _ingest_queryset(
            KbDocument.all_tenants.filter(tenant=tenant, doc_type=KbDocType.FAQ),
            force=True,
            label="test_filter",
            provider=provider,
        )
        assert counters["processed"] == 1


class TestPendingSweep:
    def test_picks_null_embedded_at(self, tenant: Tenant) -> None:
        # Two new docs — embedded_at is NULL on creation.
        _make_doc(tenant, suffix="1")
        _make_doc(tenant, suffix="2")
        provider = FakeProvider()
        # Default queryset is the embed_pending_kb_documents one.
        from django.db.models import Q

        from apps.kb.tasks import models_lookup_self_updated_at

        queryset = KbDocument.all_tenants.filter(
            Q(embedded_at__isnull=True) | Q(embedded_at__lt=models_lookup_self_updated_at()),
            tenant=tenant,
        )
        counters = _ingest_queryset(queryset, force=False, label="test_pending", provider=provider)
        assert counters["processed"] == 2
        assert counters["ingested"] == 2

    def test_skips_already_embedded(self, tenant: Tenant) -> None:
        doc = _make_doc(tenant, suffix="1")
        # Pretend this row was embedded after its last update.
        doc.embedded_at = datetime.now(timezone.utc) + timedelta(hours=1)
        doc.save(update_fields=["embedded_at"])
        provider = FakeProvider()

        from django.db.models import Q

        from apps.kb.tasks import models_lookup_self_updated_at

        queryset = KbDocument.all_tenants.filter(
            Q(embedded_at__isnull=True) | Q(embedded_at__lt=models_lookup_self_updated_at()),
            tenant=tenant,
        )
        counters = _ingest_queryset(queryset, force=False, label="test_skip", provider=provider)
        # Selector returned nothing — already-embedded row skipped.
        assert counters["processed"] == 0


class TestCeleryEntryPoints:
    """The tasks accept str(uuid) since Celery serialises UUIDs to str."""

    def test_reindex_task_callable_inline(
        self,
        tenant: Tenant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_doc(tenant, suffix="1")

        provider = FakeProvider()
        # Patch the default-provider factory so the task uses our fake.
        from apps.kb import tasks

        monkeypatch.setattr(tasks, "_build_default_provider", lambda: provider)

        counters = reindex_tenant_kb(str(tenant.id))
        assert counters["processed"] == 1
        assert counters["ingested"] == 1

    def test_pending_task_callable_inline(
        self,
        tenant: Tenant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_doc(tenant, suffix="1")
        provider = FakeProvider()
        from apps.kb import tasks

        monkeypatch.setattr(tasks, "_build_default_provider", lambda: provider)

        counters = embed_pending_kb_documents(tenant_id=str(tenant.id))
        assert counters["ingested"] == 1
