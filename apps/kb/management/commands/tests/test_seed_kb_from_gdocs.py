"""Tests for ``seed_kb_from_gdocs`` management command (KB-RAG Sub-5 / GH #118).

The command is exercised end-to-end via ``call_command`` against an
in-memory ``GoogleDocsClient`` substitute installed with ``mock.patch``.
No live Google API calls — fixtures construct
:class:`apps.kb.services.gdocs_client.GoogleDoc` objects directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO
from typing import Any
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.kb.models import KbDocType, KbDocument
from apps.kb.services.gdocs_client import GoogleDoc, GoogleDocParagraph
from apps.kb.services.global_tenant import get_global_kb_tenant
from apps.tenancy.models import Tenant


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_global_tenant_cache() -> Iterator[None]:
    """Reset the lru_cache between scenarios so each test sees fresh DB state."""
    get_global_kb_tenant.cache_clear()
    yield
    get_global_kb_tenant.cache_clear()


@pytest.fixture
def global_kb_tenant(db) -> Tenant:
    """Provision the system tenant ``seed_kb_from_gdocs`` defaults to."""
    return Tenant.objects.create(
        slug="global_kb",
        name="Global Knowledge Base",
        is_system=True,
    )


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(slug="formula-tela", name="Формула тела")


def _gdoc_h2_h3() -> GoogleDoc:
    """Doc with 3 H2 sections × 2 H3 subsections each → 6 H3 chunks total."""
    paragraphs: list[GoogleDocParagraph] = []
    for i in range(1, 4):
        paragraphs.append(GoogleDocParagraph(text=f"Раздел {i}", heading_level=2))
        for j in range(1, 3):
            paragraphs.append(GoogleDocParagraph(text=f"Под {i}.{j}", heading_level=3))
            paragraphs.append(
                GoogleDocParagraph(text=f"Тело {i}.{j} — описание.", heading_level=None)
            )
    return GoogleDoc(doc_id="doc-abc", title="Каталог услуг", paragraphs=paragraphs)


def _gdoc_no_headings() -> GoogleDoc:
    return GoogleDoc(
        doc_id="doc-flat",
        title="Plain doc",
        paragraphs=[
            GoogleDocParagraph(text="Первый абзац.", heading_level=None),
            GoogleDocParagraph(text="Второй абзац.", heading_level=None),
            GoogleDocParagraph(text="Третий абзац.", heading_level=None),
        ],
    )


def _patch_client(doc: GoogleDoc) -> Any:
    """Return a context manager that replaces ``GoogleDocsClient`` with a mock.

    The mock's ``fetch_document`` returns ``doc`` regardless of doc_id.
    """
    fake = mock.MagicMock()
    fake.fetch_document.return_value = doc
    return mock.patch(
        "apps.kb.management.commands.seed_kb_from_gdocs.GoogleDocsClient",
        return_value=fake,
    )


def _run(
    *,
    doc_id: str = "doc-abc",
    doc_type: str = "service",
    granularity: str = "subsection",
    tenant_slug: str | None = None,
    dry_run: bool = False,
) -> str:
    """Call the seed command, return captured stdout."""
    out = StringIO()
    kwargs: dict[str, Any] = {
        "doc_id": doc_id,
        "doc_type": doc_type,
        "granularity": granularity,
        "dry_run": dry_run,
        "stdout": out,
    }
    if tenant_slug is not None:
        kwargs["tenant_slug"] = tenant_slug
    call_command("seed_kb_from_gdocs", **kwargs)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_no_writes(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_h2_h3()):
            output = _run(dry_run=True)
        assert KbDocument.all_tenants.count() == 0
        assert "[CREATE]" in output
        # Summary still prints counts.
        assert "Seeded gdoc://doc-abc" in output


class TestGranularitySubsection:
    def test_creates_one_row_per_h3(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_h2_h3()):
            _run(granularity="subsection")
        rows = KbDocument.all_tenants.filter(tenant=global_kb_tenant)
        # 3 sections × 2 subsections = 6 rows.
        assert rows.count() == 6
        # Every row is doc_type=service and version=1.
        assert set(rows.values_list("doc_type", flat=True)) == {KbDocType.SERVICE}
        assert set(rows.values_list("version", flat=True)) == {1}
        # All embedded_at None — K6 picks them up.
        assert all(r.embedded_at is None for r in rows)
        # Each metadata carries gdoc_id + heading_level=3.
        for r in rows:
            assert r.metadata["gdoc_id"] == "doc-abc"
            assert r.metadata["granularity"] == "subsection"
            assert r.metadata["heading_level"] == 3


class TestRerunIdempotency:
    def test_rerun_with_same_content_is_noop(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_h2_h3()):
            _run(granularity="subsection")
        first_checksums = sorted(KbDocument.all_tenants.values_list("source_uri", "checksum"))
        with _patch_client(_gdoc_h2_h3()):
            output = _run(granularity="subsection")
        # No new rows.
        assert KbDocument.all_tenants.count() == 6
        second_checksums = sorted(KbDocument.all_tenants.values_list("source_uri", "checksum"))
        assert first_checksums == second_checksums
        assert "[SKIP]" in output
        # Summary shows "[CREATE] 0 rows" / "[UPDATE] 0 rows".
        assert "[CREATE] 0 rows" in output
        assert "[UPDATE] 0 rows" in output


class TestRerunVersionBump:
    def test_rerun_with_edited_section_bumps_version(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_h2_h3()):
            _run(granularity="subsection")

        # Mutate one paragraph in the doc and re-run.
        edited = _gdoc_h2_h3()
        new_paragraphs = list(edited.paragraphs)
        # Tweak the body of section 1 / subsection 1 (index 2).
        new_paragraphs[2] = GoogleDocParagraph(text="Тело 1.1 — НОВЫЙ текст.", heading_level=None)
        edited = GoogleDoc(doc_id=edited.doc_id, title=edited.title, paragraphs=new_paragraphs)
        with _patch_client(edited):
            _run(granularity="subsection")

        # 6 source_uris, but one of them now has 2 versions.
        per_uri: dict[str, list[int]] = {}
        for r in KbDocument.all_tenants.all():
            per_uri.setdefault(r.source_uri, []).append(r.version)
        # Exactly one source_uri has versions {1, 2}; the rest have {1}.
        v2_uris = [u for u, versions in per_uri.items() if 2 in versions]
        assert len(v2_uris) == 1, per_uri
        # The bumped row has embedded_at None (re-embed pending).
        v2_row = KbDocument.all_tenants.get(source_uri=v2_uris[0], version=2)
        assert v2_row.embedded_at is None
        # Original v=1 row is preserved (forensic history).
        v1_row = KbDocument.all_tenants.get(source_uri=v2_uris[0], version=1)
        assert "НОВЫЙ" not in v1_row.content


class TestGranularityDocument:
    def test_single_row(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_h2_h3()):
            _run(granularity="document")
        rows = KbDocument.all_tenants.all()
        assert rows.count() == 1
        row = rows.first()
        assert row is not None
        assert row.metadata["granularity"] == "document"
        # Body contains content from each paragraph.
        for needle in ("Раздел 1", "Под 1.1", "Тело 3.2"):
            assert needle in row.content


class TestGranularitySection:
    def test_one_row_per_h2(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_h2_h3()):
            _run(granularity="section")
        rows = KbDocument.all_tenants.all()
        # 3 H2 sections → 3 rows.
        assert rows.count() == 3
        # Each row's body should mention its own H3 sub-headings.
        per_uri = {r.source_uri: r for r in rows}
        # Find the row for Раздел 1.
        sec1 = next(r for r in per_uri.values() if "razdel-1" in r.source_uri)
        assert "Под 1.1" in sec1.content
        assert "Под 1.2" in sec1.content
        # Other sections shouldn't leak into it.
        assert "Под 2.1" not in sec1.content


class TestGranularityParagraph:
    def test_handles_doc_without_headings(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_no_headings()):
            _run(granularity="paragraph", doc_id="doc-flat")
        rows = KbDocument.all_tenants.all()
        # 3 non-empty paragraphs → 3 rows.
        assert rows.count() == 3
        # No section_title (no preceding heading) — but key still present.
        for r in rows:
            assert "section_title" in r.metadata


class TestInvalidArguments:
    def test_invalid_doc_type_errors(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_h2_h3()):
            with pytest.raises(CommandError, match="doc-type"):
                _run(doc_type="bogus")

    def test_invalid_tenant_slug_errors(self, global_kb_tenant: Tenant) -> None:
        with _patch_client(_gdoc_h2_h3()):
            with pytest.raises(CommandError, match="tenant slug not found"):
                _run(tenant_slug="ghost-tenant")

    def test_invalid_granularity_errors(self, global_kb_tenant: Tenant) -> None:
        # argparse rejects choices — surfaces as CommandError.
        with _patch_client(_gdoc_h2_h3()):
            with pytest.raises((CommandError, SystemExit)):
                _run(granularity="paragraphs")  # typo


class TestDefaultTenantResolution:
    def test_default_tenant_is_global_kb(self, global_kb_tenant: Tenant) -> None:
        # Don't pass --tenant-slug; command must resolve global_kb via the helper.
        with _patch_client(_gdoc_h2_h3()):
            _run(granularity="document")
        row = KbDocument.all_tenants.first()
        assert row is not None
        assert row.tenant_id == global_kb_tenant.id

    def test_missing_global_kb_tenant_errors(self, db) -> None:
        # No global_kb tenant provisioned → must raise CommandError, not crash.
        with _patch_client(_gdoc_h2_h3()):
            with pytest.raises(CommandError, match="global_kb"):
                _run(granularity="document")


class TestNonDefaultTenant:
    def test_writes_to_explicit_tenant(
        self, global_kb_tenant: Tenant, other_tenant: Tenant
    ) -> None:
        with _patch_client(_gdoc_h2_h3()):
            _run(granularity="document", tenant_slug=other_tenant.slug)
        row = KbDocument.all_tenants.first()
        assert row is not None
        assert row.tenant_id == other_tenant.id


class TestSourceUriSlug:
    def test_source_uri_is_slug_safe(self, global_kb_tenant: Tenant) -> None:
        # Cyrillic title gets transliterated; spaces become hyphens; lowercase.
        with _patch_client(_gdoc_h2_h3()):
            _run(granularity="subsection")
        uris = list(KbDocument.all_tenants.values_list("source_uri", flat=True))
        assert all(u.startswith("gdoc://doc-abc/") for u in uris)
        # Only [a-z0-9_-] after the prefix.
        import re

        suffix_re = re.compile(r"^[a-z0-9_-]+$")
        for u in uris:
            suffix = u.split("/", 3)[-1]
            assert suffix_re.match(suffix), f"non-slug suffix: {suffix!r}"
