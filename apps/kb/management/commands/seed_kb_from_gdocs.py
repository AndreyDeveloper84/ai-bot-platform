"""Seed KbDocument rows from a Google Doc (KB-RAG Sub-5 / GH #118).

Pulls one Google Doc via :class:`apps.kb.services.gdocs_client.GoogleDocsClient`,
chunks it according to ``--granularity``, and idempotently writes
:class:`apps.kb.models.KbDocument` rows into the chosen tenant
(defaulting to the system ``global_kb`` tenant via
:func:`apps.kb.services.global_tenant.get_global_kb_tenant`).

This command does **not** call the embedding pipeline synchronously. K6
Celery (:mod:`apps.kb.tasks`) sweeps every tenant for rows where
``embedded_at IS NULL OR embedded_at < updated_at`` and writes them
through to ChromaDB. The seed command only manages the source-of-truth
``KbDocument`` rows; the asynchronous re-embedder picks up changes on
its next pass.

### Decision matrix (per chunk)

==========================  ==========================================
DB state                    Action
==========================  ==========================================
no existing row             ``[CREATE]``  — write a new row at version=1
existing.checksum == new    ``[SKIP]``    — content unchanged, no-op
existing.checksum != new    ``[UPDATE]``  — write a new row at version=existing.version+1
==========================  ==========================================

We deliberately do **not** mutate the existing row. ``KbDocument`` is
versioned (``unique_together = (tenant, source_uri, version)``); each
content change appends a new version so a future replay/forensics query
can reconstruct what was retrieved at any point in time. The K6
ingester always selects the highest version per ``source_uri``.

### Why a deterministic slug in ``source_uri``

The ``gdoc://`` URI is the idempotency key. If we ever re-seeded with a
different slug for the same heading the rerun would create a parallel
row instead of versioning the existing one, leaving the old row
permanently "current" with stale content. The slug builder therefore:

1. Lowercases.
2. Transliterates Cyrillic → ASCII (via the ``transliterate`` package,
   ``ru`` table, ``reversed=True`` so что → "chto" rather than "?to").
3. Collapses every non-``[a-z0-9_-]`` run into a single hyphen and
   strips leading/trailing hyphens.

The result is stable across re-runs as long as the Google Doc heading
text is unchanged. Renaming a heading **is** an idempotency break — the
old row becomes orphaned (no longer touched by the seed) and a new row
appears under the new slug. That's the documented operator contract;
see ``docs/operations/global-kb-tenant.md``.

### Why ``--dry-run`` doesn't open a transaction

Dry-run prints the decision per row and reads existing checksums from
the DB. It writes nothing. Wrapping it in ``transaction.atomic()``
would be a no-op; we skip the wrapper to keep the dry-run path obvious.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from transliterate import translit  # type: ignore[import-untyped]
from transliterate.exceptions import (  # type: ignore[import-untyped]
    LanguageDetectionError,
)

from apps.kb.constants import GLOBAL_KB_TENANT_SLUG
from apps.kb.models import KbDocType, KbDocument
from apps.kb.services.gdocs_client import (
    GoogleDoc,
    GoogleDocParagraph,
    GoogleDocsClient,
)
from apps.kb.services.global_tenant import get_global_kb_tenant
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


_GRANULARITY_CHOICES = ("document", "section", "subsection", "paragraph")
# Heading level that defines a chunk boundary for each granularity.
# ``document`` and ``paragraph`` don't use this map.
_BOUNDARY_LEVEL: dict[str, int] = {
    "section": 2,
    "subsection": 3,
}


class Command(BaseCommand):
    help = (
        "Seed KbDocument rows from a Google Doc. Reads via GoogleDocsClient, "
        "chunks per --granularity, idempotent per (tenant, source_uri). "
        "Embedding is asynchronous — K6 Celery picks up the new rows."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--doc-id",
            required=True,
            help="Google Doc ID (the long opaque string from the doc URL).",
        )
        parser.add_argument(
            "--doc-type",
            required=True,
            choices=[c.value for c in KbDocType],
            help=(f"KbDocument doc_type — one of {', '.join(c.value for c in KbDocType)}."),
        )
        parser.add_argument(
            "--tenant-slug",
            default=None,
            help=(
                "Target tenant slug. Defaults to the system "
                f"{GLOBAL_KB_TENANT_SLUG!r} tenant resolved via "
                "get_global_kb_tenant(). Lookup uses Tenant.all_objects "
                "so deactivated rows still match."
            ),
        )
        parser.add_argument(
            "--granularity",
            required=True,
            choices=_GRANULARITY_CHOICES,
            help=(
                "Chunking strategy: 'document' = single row, "
                "'section' = one row per H2, 'subsection' = one row per H3, "
                "'paragraph' = one row per non-empty paragraph."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print [CREATE] / [UPDATE] / [SKIP] per row without writing.",
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        doc_id: str = options["doc_id"]
        doc_type: str = options["doc_type"]
        tenant_slug: str | None = options.get("tenant_slug")
        granularity: str = options["granularity"]
        dry_run: bool = bool(options["dry_run"])

        tenant = self._resolve_tenant(tenant_slug)
        doc = self._fetch_doc(doc_id)
        chunks = self._chunk(doc, granularity)

        if dry_run:
            self._apply(
                tenant=tenant,
                doc_id=doc_id,
                doc_type=doc_type,
                granularity=granularity,
                chunks=chunks,
                dry_run=True,
            )
            return

        # Atomic: a crash mid-doc must not leave half a doc seeded.
        with transaction.atomic():
            self._apply(
                tenant=tenant,
                doc_id=doc_id,
                doc_type=doc_type,
                granularity=granularity,
                chunks=chunks,
                dry_run=False,
            )

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _resolve_tenant(self, slug: str | None) -> Tenant:
        if slug is None:
            tenant = get_global_kb_tenant()
            if tenant is None:
                raise CommandError(
                    f"global_kb tenant not provisioned (slug={GLOBAL_KB_TENANT_SLUG!r}). "
                    "Run: python manage.py create_tenant --slug global_kb "
                    "--name 'Global Knowledge Base' --system"
                )
            return tenant

        tenant = Tenant.all_objects.filter(slug=slug).first()
        if tenant is None:
            raise CommandError(f"tenant slug not found: {slug!r}")
        return tenant

    def _fetch_doc(self, doc_id: str) -> GoogleDoc:
        # GoogleDocsClient reads the credentials path from an env var.
        # We pass it explicitly so the operator gets a clear FileNotFoundError
        # → CommandError translation if the file's missing.
        creds_path = os.environ.get("GOOGLE_DOCS_SERVICE_ACCOUNT_FILE")
        try:
            client = GoogleDocsClient(credentials_path=creds_path)
            return client.fetch_document(doc_id)
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk(self, doc: GoogleDoc, granularity: str) -> list[tuple[str, str, int | None]]:
        """Return a list of ``(title, body, heading_level)`` tuples.

        ``title`` is used as both the human label (metadata.section_title)
        and the slug source for ``source_uri``.
        ``heading_level`` is the H-level of the chunk's defining heading
        (None for ``document``, ``paragraph``, or "preamble" chunks).
        """
        if granularity == "document":
            return [self._chunk_document(doc)]
        if granularity == "paragraph":
            return self._chunk_paragraph(doc)
        # section / subsection
        return self._chunk_by_heading(doc, _BOUNDARY_LEVEL[granularity])

    @staticmethod
    def _chunk_document(doc: GoogleDoc) -> tuple[str, str, int | None]:
        body = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        title = doc.title or doc.doc_id
        return (title, body, None)

    @staticmethod
    def _chunk_paragraph(
        doc: GoogleDoc,
    ) -> list[tuple[str, str, int | None]]:
        """One row per non-empty paragraph; carry the nearest heading text."""
        chunks: list[tuple[str, str, int | None]] = []
        section_title = ""
        idx = 0
        for p in doc.paragraphs:
            if not p.text.strip():
                continue
            if p.heading_level is not None:
                # Headings themselves don't become paragraph rows — they
                # only update the section_title that subsequent paragraphs
                # carry as context.
                section_title = p.text
                continue
            idx += 1
            # Title used for the slug: prefer the nearest heading; fall
            # back to a stable positional slug so reruns line up.
            slug_source = section_title or f"paragraph-{idx}"
            # The chunk's "title" is what shows up in metadata.section_title;
            # body is just the paragraph text.
            chunks.append((f"{slug_source}-{idx}", p.text, None))
        return chunks

    @staticmethod
    def _chunk_by_heading(doc: GoogleDoc, boundary_level: int) -> list[tuple[str, str, int | None]]:
        """Walk paragraphs, starting a new chunk at each H<boundary_level>.

        Paragraphs before the first boundary heading attach to a
        synthetic 'preamble' chunk *if* non-empty. Deeper-level headings
        (e.g. H3 inside an H2 section when chunking at section level) are
        included verbatim in the chunk body — they're context, not
        boundaries at this granularity.

        For ``subsection`` (boundary=H3) with an H2-only document:
        every section has no H3, so each section falls through to a
        single chunk covering the whole section. That's the
        "don't lose content" fallback.
        """
        chunks: list[tuple[str, str, int | None]] = []
        # Walk H2 sections first (so subsection chunking can fall back
        # gracefully when a section has no H3 inside it).
        sections = _split_by_h2(doc.paragraphs)
        for section_title, section_paragraphs in sections:
            if boundary_level == 2:
                # ``section`` granularity: one chunk per H2 section.
                body = _paragraphs_to_body(section_paragraphs)
                if not body.strip():
                    continue
                chunks.append((section_title, body, 2))
                continue
            # boundary_level == 3 → subsection granularity inside this section.
            sub_chunks = _split_by_h3(section_paragraphs)
            if not sub_chunks:
                # No H3 inside this section: emit the whole section as
                # one chunk so content isn't lost.
                body = _paragraphs_to_body(section_paragraphs)
                if body.strip():
                    chunks.append((section_title, body, 2))
                continue
            for sub_title, sub_paragraphs in sub_chunks:
                body = _paragraphs_to_body(sub_paragraphs)
                if not body.strip():
                    continue
                # Title space prefix the section to keep slugs unique even
                # when two H2s have the same H3 name (e.g. "Цены").
                composite = f"{section_title}-{sub_title}" if section_title else sub_title
                chunks.append((composite, body, 3))
        return chunks

    # ------------------------------------------------------------------
    # Apply chunks to DB (or dry-run print)
    # ------------------------------------------------------------------

    def _apply(
        self,
        *,
        tenant: Tenant,
        doc_id: str,
        doc_type: str,
        granularity: str,
        chunks: list[tuple[str, str, int | None]],
        dry_run: bool,
    ) -> None:
        created = updated = skipped = 0

        for title, body, heading_level in chunks:
            source_uri = f"gdoc://{doc_id}/{_slugify(title)}"
            proposed_checksum = KbDocument.compute_checksum(body)
            existing = (
                KbDocument.all_tenants.filter(tenant=tenant, source_uri=source_uri)
                .order_by("-version")
                .first()
            )

            decision: str
            if existing is None:
                decision = "[CREATE]"
                created += 1
                if not dry_run:
                    KbDocument.all_tenants.create(
                        tenant=tenant,
                        doc_type=doc_type,
                        source_uri=source_uri,
                        version=1,
                        content=body,
                        checksum=proposed_checksum,
                        metadata={
                            "gdoc_id": doc_id,
                            "section_title": title,
                            "granularity": granularity,
                            "heading_level": heading_level,
                        },
                    )
            elif existing.checksum == proposed_checksum:
                decision = "[SKIP]"
                skipped += 1
            else:
                decision = "[UPDATE]"
                updated += 1
                if not dry_run:
                    KbDocument.all_tenants.create(
                        tenant=tenant,
                        doc_type=doc_type,
                        source_uri=source_uri,
                        version=existing.version + 1,
                        content=body,
                        checksum=proposed_checksum,
                        embedded_at=None,
                        metadata={
                            "gdoc_id": doc_id,
                            "section_title": title,
                            "granularity": granularity,
                            "heading_level": heading_level,
                        },
                    )

            self.stdout.write(f"  {decision} {source_uri}")

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            f"{prefix}Seeded gdoc://{doc_id} doc_type={doc_type} "
            f"tenant={tenant.slug} granularity={granularity}:\n"
            f"  [CREATE] {created} rows\n"
            f"  [UPDATE] {updated} rows\n"
            f"  [SKIP] {skipped} rows"
        )


# ---------------------------------------------------------------------------
# Module-level helpers (kept module-level so they're trivially unit-testable
# without instantiating the Command).
# ---------------------------------------------------------------------------


def _split_by_h2(
    paragraphs: list[GoogleDocParagraph],
) -> list[tuple[str, list[GoogleDocParagraph]]]:
    """Group paragraphs by H2 boundary.

    Returns a list of ``(section_title, paragraphs_in_section)`` where
    ``paragraphs_in_section`` does NOT include the H2 heading itself.
    Paragraphs before the first H2 attach to a synthetic "preamble"
    entry only if at least one non-empty paragraph exists there.
    """
    out: list[tuple[str, list[GoogleDocParagraph]]] = []
    preamble: list[GoogleDocParagraph] = []
    current_title: str | None = None
    current: list[GoogleDocParagraph] = []
    for p in paragraphs:
        if p.heading_level == 2:
            if current_title is not None:
                out.append((current_title, current))
            current_title = p.text
            current = []
            continue
        if current_title is None:
            preamble.append(p)
        else:
            current.append(p)
    if current_title is not None:
        out.append((current_title, current))
    if any(p.text.strip() for p in preamble):
        # Preamble surfaces first so reruns line up consistently.
        out.insert(0, ("preamble", preamble))
    return out


def _split_by_h3(
    paragraphs: list[GoogleDocParagraph],
) -> list[tuple[str, list[GoogleDocParagraph]]]:
    """Group paragraphs inside an H2 section by H3 boundary.

    Returns ``[]`` if the section contains no H3 — the caller falls back
    to emitting the whole section as a single chunk.
    """
    out: list[tuple[str, list[GoogleDocParagraph]]] = []
    current_title: str | None = None
    current: list[GoogleDocParagraph] = []
    for p in paragraphs:
        if p.heading_level == 3:
            if current_title is not None:
                out.append((current_title, current))
            current_title = p.text
            current = []
            continue
        if current_title is not None:
            current.append(p)
        # If we haven't seen an H3 yet, ignore the paragraph — the
        # outer fallback emits the whole section when no H3 exists.
    if current_title is not None:
        out.append((current_title, current))
    return out


def _paragraphs_to_body(paragraphs: list[GoogleDocParagraph]) -> str:
    """Render paragraphs back to a body string, dropping empties."""
    return "\n\n".join(p.text for p in paragraphs if p.text.strip())


# Regex used by ``_slugify`` to collapse non-slug characters.
_SLUG_NONWORD_RE = re.compile(r"[^a-z0-9_-]+")
_SLUG_HYPHEN_RUN_RE = re.compile(r"-{2,}")


def _slugify(text: str) -> str:
    """Deterministic slug builder for ``source_uri``.

    Steps:
      1. Transliterate Cyrillic → ASCII via the ``transliterate`` library
         with ``reversed=True`` (Cyrillic → Latin direction).
      2. Lowercase.
      3. Replace every non-``[a-z0-9_-]`` run with a single hyphen.
      4. Collapse consecutive hyphens and strip leading/trailing.

    Empty / fully-non-ASCII inputs fall back to ``"untitled"`` so the
    URI is always a valid slug.
    """
    if not text:
        return "untitled"
    # ``transliterate.translit`` raises LanguageDetectionError when the
    # input has no recognisable Cyrillic characters — that's fine, our
    # plain-ASCII path doesn't need transliteration.
    try:
        ascii_text = translit(text, "ru", reversed=True)
    except LanguageDetectionError:
        ascii_text = text
    ascii_text = ascii_text.lower()
    ascii_text = _SLUG_NONWORD_RE.sub("-", ascii_text)
    ascii_text = _SLUG_HYPHEN_RUN_RE.sub("-", ascii_text)
    ascii_text = ascii_text.strip("-")
    return ascii_text or "untitled"
