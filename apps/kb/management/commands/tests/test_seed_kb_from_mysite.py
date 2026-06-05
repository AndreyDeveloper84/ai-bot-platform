"""Tests for ``seed_kb_from_mysite`` management command (F5 / KB content pipe).

Exercises every code branch end-to-end via ``call_command`` against
in-memory JSON fixtures (no mysite Postgres dependency). The platform
side doesn't care WHERE the manifest came from — only that the shape
matches the documented contract.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.kb.models import KbDocType, KbDocument
from apps.tenancy.models import Tenant


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="formula-tela", name="Формула тела")


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path per section
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_services_imported_with_service_doc_type(tenant, tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "services": [
                {
                    "id": 42,
                    "name": "Антицеллюлитный массаж",
                    "description": "60 минут глубокой проработки.",
                    "category": "Массаж",
                    "duration_minutes": 60,
                    "price": 3500.0,
                },
            ],
        },
    )

    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))

    doc = KbDocument.all_tenants.get(source_uri="mysite://services/42")
    assert doc.doc_type == KbDocType.SERVICE
    # Body leads with the service name so the retriever's keyword
    # overlap fires before the descriptive prose.
    assert doc.content.startswith("Услуга: Антицеллюлитный массаж")
    assert "60 минут глубокой проработки." in doc.content
    assert doc.metadata["mysite_id"] == 42
    assert doc.metadata["category"] == "Массаж"
    assert doc.metadata["duration_minutes"] == 60
    assert doc.metadata["price"] == 3500.0
    # K4 sweep picks this up.
    assert doc.embedded_at is None


@pytest.mark.django_db
def test_masters_imported_with_master_doc_type(tenant, tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "masters": [
                {
                    "id": 1,
                    "name": "Анна Иванова",
                    "bio": "Сертифицированный массажист, 8 лет стажа.",
                },
            ],
        },
    )

    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))

    doc = KbDocument.all_tenants.get(source_uri="mysite://masters/1")
    assert doc.doc_type == KbDocType.MASTER
    assert doc.content.startswith("Мастер: Анна Иванова")
    assert "8 лет стажа" in doc.content


@pytest.mark.django_db
def test_site_settings_split_into_separate_docs(tenant, tmp_path):
    """One KbDocument per populated SiteSettings field — empty fields
    are skipped silently. Contact info lands as FAQ, policy text as LEGAL."""
    manifest = _write_manifest(
        tmp_path,
        {
            "site_settings": {
                "salon_name": "Формула тела",
                "phone": "8 (8412) 39-34-33",
                "email": "info@example.com",
                "address": "Пенза, Ул. Пушкина, 45",
                "working_hours": "Ежедневно с 9.00 до 21.00",
                "cancellation_policy": "Отмена за 24 часа без штрафа.",
                "privacy_policy": "",  # empty → skipped
            },
        },
    )

    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))

    docs = {d.source_uri: d for d in KbDocument.all_tenants.filter(tenant=tenant)}
    # 4 contact fields + 1 policy = 5 docs (privacy was empty → skipped)
    assert len(docs) == 5
    assert "mysite://site/address" in docs
    assert "mysite://site/phone" in docs
    assert "mysite://site/email" in docs
    assert "mysite://site/working_hours" in docs
    assert "mysite://site/cancellation_policy" in docs
    assert "mysite://site/privacy_policy" not in docs

    # Contact info = FAQ; policy = LEGAL.
    assert docs["mysite://site/address"].doc_type == KbDocType.FAQ
    assert docs["mysite://site/cancellation_policy"].doc_type == KbDocType.LEGAL

    # Question-prefix shape primes RAG retrieval.
    assert docs["mysite://site/address"].content.startswith("Адрес салона:")
    assert "Пенза, Ул. Пушкина, 45" in docs["mysite://site/address"].content


@pytest.mark.django_db
def test_promotions_imported_with_faq_doc_type(tenant, tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "promotions": [
                {
                    "id": 7,
                    "name": "Скидка 20% на первый визит",
                    "description": "Действует до конца месяца.",
                },
            ],
        },
    )

    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))

    doc = KbDocument.all_tenants.get(source_uri="mysite://promotions/7")
    assert doc.doc_type == KbDocType.FAQ
    assert doc.content.startswith("Акция: Скидка 20%")
    assert doc.metadata["kind"] == "promotion"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_rerun_with_identical_content_is_noop(tenant, tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "services": [
                {"id": 1, "name": "Массаж", "description": "Описание."},
            ],
        },
    )

    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))
    doc_v1 = KbDocument.all_tenants.get(source_uri="mysite://services/1")
    pk = doc_v1.pk
    original_checksum = doc_v1.checksum
    original_updated = doc_v1.updated_at

    # Mark embedded so we can detect whether the upsert nulled it.
    KbDocument.all_tenants.filter(pk=pk).update(embedded_at=doc_v1.updated_at)

    # Second run — same content, must not touch the row.
    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))
    doc_v2 = KbDocument.all_tenants.get(pk=pk)
    assert doc_v2.checksum == original_checksum
    assert doc_v2.updated_at == original_updated
    # Critically: embedded_at NOT nulled — re-embed wasn't triggered.
    assert doc_v2.embedded_at is not None


@pytest.mark.django_db
def test_rerun_with_drifted_content_nulls_embedded_at(tenant, tmp_path):
    """Content edit on mysite → re-embed required → embedded_at = NULL."""
    manifest_v1 = _write_manifest(
        tmp_path,
        {"services": [{"id": 1, "name": "Массаж", "description": "Первая версия."}]},
    )
    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest_v1))

    doc = KbDocument.all_tenants.get(source_uri="mysite://services/1")
    KbDocument.all_tenants.filter(pk=doc.pk).update(embedded_at=doc.updated_at)

    # Edit the description in mysite → manifest changes.
    manifest_v2 = _write_manifest(
        tmp_path,
        {"services": [{"id": 1, "name": "Массаж", "description": "Новая версия с правками."}]},
    )
    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest_v2))

    doc_after = KbDocument.all_tenants.get(pk=doc.pk)
    assert "Новая версия с правками." in doc_after.content
    assert doc_after.checksum != doc.checksum
    # K4 sweep must re-embed.
    assert doc_after.embedded_at is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dry_run_writes_nothing(tenant, tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {"services": [{"id": 1, "name": "Массаж", "description": "..."}]},
    )

    out = StringIO()
    call_command(
        "seed_kb_from_mysite",
        "--tenant",
        "formula-tela",
        "--source",
        str(manifest),
        "--dry-run",
        stdout=out,
    )

    assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0
    assert "would import" in out.getvalue()


@pytest.mark.django_db
def test_empty_sections_skipped_silently(tenant, tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {"services": [], "masters": [], "site_settings": {}, "promotions": []},
    )
    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))
    assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0


@pytest.mark.django_db
def test_service_without_name_or_description_skipped(tenant, tmp_path):
    """Defensive: malformed records don't blow up the run, they get dropped."""
    manifest = _write_manifest(
        tmp_path,
        {
            "services": [
                {"id": 1, "name": "", "description": "no name"},
                {"id": 2, "name": "no desc", "description": ""},
                {"id": 3, "name": "valid", "description": "valid"},
            ],
        },
    )
    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))
    docs = KbDocument.all_tenants.filter(tenant=tenant)
    assert docs.count() == 1
    assert docs.first().source_uri == "mysite://services/3"


@pytest.mark.django_db
def test_unknown_tenant_raises(tmp_path):
    manifest = _write_manifest(tmp_path, {"services": []})
    with pytest.raises(CommandError, match="tenant slug not found"):
        call_command(
            "seed_kb_from_mysite",
            "--tenant",
            "nonexistent",
            "--source",
            str(manifest),
        )


@pytest.mark.django_db
def test_missing_source_raises(tenant, tmp_path):
    with pytest.raises(CommandError, match="source manifest not found"):
        call_command(
            "seed_kb_from_mysite",
            "--tenant",
            "formula-tela",
            "--source",
            str(tmp_path / "nope.json"),
        )


@pytest.mark.django_db
def test_malformed_json_raises(tenant, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(CommandError, match="malformed JSON"):
        call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(bad))


@pytest.mark.django_db
def test_root_must_be_object(tenant, tmp_path):
    """JSON array at root is invalid — operator likely confused with
    Django dumpdata format. Helpful error message."""
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(CommandError, match="manifest root must be a JSON object"):
        call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(bad))


@pytest.mark.django_db
def test_tenant_isolation(tenant, tmp_path):
    """Import to tenant A must not leak into tenant B."""
    other = Tenant.objects.create(slug="other-salon", name="Other")
    manifest = _write_manifest(
        tmp_path,
        {"services": [{"id": 1, "name": "Массаж", "description": "..."}]},
    )

    call_command("seed_kb_from_mysite", "--tenant", "formula-tela", "--source", str(manifest))

    assert KbDocument.all_tenants.filter(tenant=tenant).count() == 1
    assert KbDocument.all_tenants.filter(tenant=other).count() == 0


# ---------------------------------------------------------------------------
# Full multi-section import
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_multi_section_manifest_imports_everything(tenant, tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "services": [
                {"id": 1, "name": "Массаж", "description": "60 минут."},
                {"id": 2, "name": "VelaShape", "description": "Аппаратная коррекция."},
            ],
            "masters": [
                {"id": 1, "name": "Анна", "bio": "8 лет стажа."},
            ],
            "site_settings": {
                "address": "Пенза, Пушкина 45",
                "phone": "8 (8412) 39-34-33",
            },
            "promotions": [
                {"id": 1, "name": "Первый визит -20%", "description": "До конца месяца."},
            ],
        },
    )

    out = StringIO()
    call_command(
        "seed_kb_from_mysite",
        "--tenant",
        "formula-tela",
        "--source",
        str(manifest),
        stdout=out,
    )

    # 2 services + 1 master + 2 site fields + 1 promotion = 6
    assert KbDocument.all_tenants.filter(tenant=tenant).count() == 6
    output = out.getvalue()
    assert "services: 2 imported" in output
    assert "masters: 1 imported" in output
    assert "site_settings: 2 imported" in output
    assert "promotions: 1 imported" in output
    assert "imported 6 doc(s)" in output
