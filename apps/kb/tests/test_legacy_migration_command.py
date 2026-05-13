"""Legacy KB migration command tests (DRF-566 / Sprint 7 / K8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.kb.models import KbDocType, KbDocument
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="legacy-mig", name="Legacy Mig")


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    return tmp_path / "help_articles.json"


def _write(manifest_path: Path, records: list[dict]) -> Path:
    manifest_path.write_text(json.dumps(records), encoding="utf-8")
    return manifest_path


class TestPlainManifest:
    def test_creates_kb_documents(self, tenant: Tenant, manifest_path: Path) -> None:
        _write(
            manifest_path,
            [
                {"id": 1, "question": "Часы работы?", "answer": "Пн-Вс 10-22"},
                {"id": 2, "question": "Адрес?", "answer": "Пенза, ул. Ленина 1"},
            ],
        )
        call_command(
            "migrate_legacy_kb",
            tenant=tenant.slug,
            source=str(manifest_path),
        )
        docs = KbDocument.all_tenants.filter(tenant=tenant, doc_type=KbDocType.HELP_ARTICLE)
        assert docs.count() == 2
        uris = sorted(docs.values_list("source_uri", flat=True))
        assert uris == [
            "mysite://help-articles/1",
            "mysite://help-articles/2",
        ]

    def test_re_run_is_idempotent(self, tenant: Tenant, manifest_path: Path) -> None:
        _write(
            manifest_path,
            [{"id": 1, "question": "Q", "answer": "A"}],
        )
        call_command("migrate_legacy_kb", tenant=tenant.slug, source=str(manifest_path))
        call_command("migrate_legacy_kb", tenant=tenant.slug, source=str(manifest_path))
        # Still exactly one row — UPSERT semantics by (tenant, source_uri, version).
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 1


class TestDjangoFixtureManifest:
    def test_accepts_dumpdata_shape(self, tenant: Tenant, manifest_path: Path) -> None:
        # `python manage.py dumpdata services_app.HelpArticle` output shape.
        _write(
            manifest_path,
            [
                {
                    "model": "services_app.helparticle",
                    "pk": 7,
                    "fields": {
                        "question": "Цена?",
                        "answer": "1500₽",
                        "is_active": True,
                    },
                }
            ],
        )
        call_command("migrate_legacy_kb", tenant=tenant.slug, source=str(manifest_path))
        doc = KbDocument.all_tenants.get(tenant=tenant, source_uri="mysite://help-articles/7")
        assert "1500" in doc.content
        assert doc.metadata["legacy_id"] == 7


class TestContentEdit:
    def test_changed_content_updates_and_clears_embedded_at(
        self, tenant: Tenant, manifest_path: Path
    ) -> None:
        _write(manifest_path, [{"id": 1, "question": "Q", "answer": "Old"}])
        call_command("migrate_legacy_kb", tenant=tenant.slug, source=str(manifest_path))

        # Mark as embedded to simulate a normal state-of-affairs.
        from datetime import datetime, timezone

        doc = KbDocument.all_tenants.get(tenant=tenant, source_uri="mysite://help-articles/1")
        doc.embedded_at = datetime.now(timezone.utc)
        doc.save(update_fields=["embedded_at"])

        # Edit the manifest + re-run.
        _write(manifest_path, [{"id": 1, "question": "Q", "answer": "New"}])
        call_command("migrate_legacy_kb", tenant=tenant.slug, source=str(manifest_path))

        doc.refresh_from_db()
        assert "New" in doc.content
        # Cleared so K4 ingester re-embeds.
        assert doc.embedded_at is None


class TestDryRun:
    def test_dry_run_writes_nothing(self, tenant: Tenant, manifest_path: Path) -> None:
        _write(manifest_path, [{"id": 1, "question": "Q", "answer": "A"}])
        call_command(
            "migrate_legacy_kb",
            tenant=tenant.slug,
            source=str(manifest_path),
            dry_run=True,
        )
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0


class TestErrorPaths:
    def test_missing_tenant_raises(self, db, manifest_path: Path) -> None:
        _write(manifest_path, [{"id": 1, "question": "Q", "answer": "A"}])
        with pytest.raises(CommandError, match="tenant slug not found"):
            call_command(
                "migrate_legacy_kb",
                tenant="ghost",
                source=str(manifest_path),
            )

    def test_missing_source_raises(self, tenant: Tenant, tmp_path: Path) -> None:
        with pytest.raises(CommandError, match="source manifest not found"):
            call_command(
                "migrate_legacy_kb",
                tenant=tenant.slug,
                source=str(tmp_path / "missing.json"),
            )

    def test_malformed_json_raises(self, tenant: Tenant, manifest_path: Path) -> None:
        manifest_path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(CommandError, match="malformed JSON"):
            call_command(
                "migrate_legacy_kb",
                tenant=tenant.slug,
                source=str(manifest_path),
            )

    def test_non_list_payload_raises(self, tenant: Tenant, manifest_path: Path) -> None:
        manifest_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        with pytest.raises(CommandError, match="must be a JSON list"):
            call_command(
                "migrate_legacy_kb",
                tenant=tenant.slug,
                source=str(manifest_path),
            )


class TestEmptyRowsSkipped:
    def test_empty_question_and_answer_skipped(self, tenant: Tenant, manifest_path: Path) -> None:
        _write(
            manifest_path,
            [
                {"id": 1, "question": "", "answer": ""},
                {"id": 2, "question": "Q", "answer": "A"},
            ],
        )
        call_command("migrate_legacy_kb", tenant=tenant.slug, source=str(manifest_path))
        # Only the non-empty row landed.
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 1
