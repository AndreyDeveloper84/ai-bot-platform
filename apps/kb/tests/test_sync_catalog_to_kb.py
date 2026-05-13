"""sync_catalog_to_kb Celery task tests (DRF-565 / Sprint 7 / K7)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from apps.catalog.models import CatalogFaq, CatalogService
from apps.kb.chromadb_client import reset_client_cache
from apps.kb.models import KbDocType, KbDocument
from apps.kb.tasks import sync_catalog_to_kb
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
    return Tenant.objects.create(slug="k7", name="K7")


def _ts() -> datetime:
    return datetime(2026, 5, 13, tzinfo=timezone.utc)


class FakeProvider:
    name = "fake"
    default_embedding_model = "fake-embed-3"

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        return [((ord(text[i % len(text)]) if text else 1) % 100) / 100.0 for i in range(8)]

    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


class TestChain:
    def test_first_run_creates_kb_rows_and_embeds(
        self,
        tenant: Tenant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed catalog mirrors for the tenant.
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1,
            external_updated_at=_ts(),
            slug="s1",
            name="Service 1",
            description="Описание",
        )
        CatalogFaq.all_tenants.create(
            tenant=tenant,
            external_id=2,
            external_updated_at=_ts(),
            question="Q?",
            answer="A",
        )

        # Patch the default provider builder so the K6 embed step uses fake.
        from apps.kb import tasks

        monkeypatch.setattr(tasks, "_build_default_provider", lambda: FakeProvider())

        result = sync_catalog_to_kb(str(tenant.id))

        # Per-doc-type projection counters land in the result.
        assert result[f"{KbDocType.SERVICE}_created"] == 1
        assert result[f"{KbDocType.FAQ}_created"] == 1
        # 2 KB rows created.
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 2
        # Embed phase ingested both.
        assert result["embed_ingested"] == 2

    def test_second_run_no_changes_no_embed_spend(
        self,
        tenant: Tenant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1,
            external_updated_at=_ts(),
            slug="s",
            name="Service",
            description="x",
        )
        provider = FakeProvider()
        from apps.kb import tasks

        monkeypatch.setattr(tasks, "_build_default_provider", lambda: provider)

        sync_catalog_to_kb(str(tenant.id))
        embed_calls_after_first = 0  # track via patching embed?

        # Patch the ingester's embedding helper to count calls on the
        # second pass.
        with patch("apps.kb.services.ingester._sync_embed") as mock_embed:
            mock_embed.return_value = [0.1] * 8
            second = sync_catalog_to_kb(str(tenant.id))

        assert second[f"{KbDocType.SERVICE}_unchanged"] == 1
        assert second[f"{KbDocType.SERVICE}_created"] == 0
        # No new embed calls — checksum unchanged + selector skipped.
        # (The K6 selector uses embedded_at >= updated_at, so re-running
        # right after the first ingest doesn't re-pick these rows.)
        # Note: _sync_embed may be called 0 times here.
        assert mock_embed.call_count == 0
        _ = embed_calls_after_first  # silence unused

    def test_content_edit_re_embeds(
        self,
        tenant: Tenant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        svc = CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1,
            external_updated_at=_ts(),
            slug="s",
            name="Service",
            description="Старое",
        )
        from apps.kb import tasks

        monkeypatch.setattr(tasks, "_build_default_provider", lambda: FakeProvider())
        sync_catalog_to_kb(str(tenant.id))

        # Upstream edit — catalog mirror description changed.
        svc.description = "Новое описание"
        svc.save()

        second = sync_catalog_to_kb(str(tenant.id))
        assert second[f"{KbDocType.SERVICE}_updated"] == 1
        # The KB row's content updated to reflect.
        kb = KbDocument.all_tenants.get(tenant=tenant, source_uri="mysite://services/1")
        assert "Новое описание" in kb.content
