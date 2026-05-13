"""Knowledge-base document model (DRF-558 / Sprint 7 / K1).

A :class:`KbDocument` is one chunkable source artefact owned by a
tenant. Sprint 7 / K3 (DRF-561) chunks each document into ~512-token
pieces; K4 (DRF-562) embeds them into the tenant's ChromaDB collection;
K5 (DRF-563) is the retrieval entrypoint the FAQ skill calls.

### Why the document layer at all (instead of going straight to chunks)

* **Idempotent re-ingest**: catalog sync (C-track / DRF-572..579) writes
  the canonical source row; the KB job reads it, splits, and re-embeds.
  Without a document layer we'd embed the same Service description N
  times across N runs and pollute ChromaDB.
* **Change detection cheap**: the SHA-256 ``checksum`` on
  ``(source_uri, version)`` tells the ingester "skip — content
  unchanged, embedding still valid" without touching ChromaDB.
* **Provenance**: when a chunk surfaces in a retrieved answer the
  ``source_uri`` is the audit trail back to the live mysite row the
  operator can edit.

### doc_type choices

Locked at six in Sprint 7 — wider categories (legal, contraindication)
folded into existing apps.catalog mirrors live as text in dedicated
KbDocument rows because they don't map to a structured mysite model.

Adding a new type is a migration + an entry here; the chunker and
retriever both branch on ``doc_type`` (different chunking strategies
for FAQ short rows vs Service long descriptions).

### tenant FK CASCADE

KB chunks are cheap to re-ingest from the catalog mirror. If a tenant
is deleted, every KbDocument + their ChromaDB collection (K11 / DRF-569
signal) goes away too. PROTECT (used for BotUser) would force operators
to drop forensic-relevant data first; here that's overkill.
"""

from __future__ import annotations

import hashlib
import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class KbDocType(models.TextChoices):
    """Locked six doc-type slugs (Sprint 7 / K1).

    These keys propagate into the ChromaDB metadata payload — chunker
    + retriever + replay redactor all branch on them. Adding a new
    type requires a new migration plus updating the chunker strategy
    table in K3 (DRF-561).
    """

    SERVICE = "service", "Service description"
    MASTER = "master", "Master bio / specialisation"
    CONTRAINDICATION = "contraindication", "Health screening contraindication"
    FAQ = "faq", "Frequently-asked-question entry"
    HELP_ARTICLE = "help_article", "Help-article long-form"
    LEGAL = "legal", "Legal / policy text"


class KbDocument(models.Model):
    """One chunkable source artefact in the tenant knowledge base.

    Per PHASE0_DESIGN §3.8 schema. Fields:

      * ``tenant`` — owning Tenant. CASCADE: re-ingest is cheap.
      * ``doc_type`` — one of :class:`KbDocType`. Drives the chunking
        strategy and metadata payload written into ChromaDB.
      * ``source_uri`` — stable provenance key. For catalog-mirrored
        rows: ``mysite://services/<external_id>``,
        ``mysite://masters/<external_id>``, etc. For manual rows
        (legal text, hand-curated FAQ): a free-form slug.
      * ``version`` — monotonic int per ``source_uri``. Increment when
        upstream content changes; old versions stay queryable for
        replay forensics. Combined with checksum: change detection.
      * ``locale`` — IETF tag. Sprint 7 ships ``ru-RU`` only; field
        is here so Phase 1 can add ``en-US`` without a migration.
      * ``metadata`` — JSONField for free-form per-doc context
        (master IDs referenced, parent service slug, …). Surfaces
        in ChromaDB metadata payload.
      * ``checksum`` — SHA-256 of ``content``. K4 (DRF-562) compares
        before embedding to skip unchanged docs.
      * ``content`` — full text. Source-of-truth; chunks rebuilt from
        this on each ingest.
      * ``embedded_at`` — nullable timestamp. NULL = needs embedding
        (initial state or after content edit). K4 ingester picks rows
        where ``embedded_at IS NULL OR embedded_at < updated_at``.
      * ``created_at`` / ``updated_at`` — standard audit timestamps.

    ### Uniqueness

    ``(tenant, source_uri, version)`` is unique. The same external
    row can have multiple historical versions; queries for "current"
    state filter ``version = MAX(version) WHERE source_uri = ?``.

    ### Indexes

    ``(tenant, doc_type, updated_at)`` accelerates the most common
    K4 ingester query: "docs of type X in tenant Y modified since Z".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="kb_documents",
        help_text="Owning tenant. CASCADE — KB content is cheap to "
        "re-ingest from the catalog mirror; tenant delete also wipes "
        "the ChromaDB collection (K11 signal).",
    )
    doc_type = models.CharField(
        max_length=32,
        choices=KbDocType.choices,
        help_text="Locked six-value taxonomy. Drives chunking strategy "
        "(K3) and ChromaDB metadata payload (K4).",
    )
    source_uri = models.CharField(
        max_length=255,
        help_text="Stable provenance key. Catalog-mirrored: "
        "`mysite://services/<external_id>`. Manual: free-form slug.",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Monotonic per source_uri. Bumped when upstream "
        "content changes; old versions stay queryable for forensics.",
    )
    locale = models.CharField(
        max_length=16,
        default="ru-RU",
        help_text="IETF locale tag. Sprint 7 ships ru-RU only; field "
        "reserved so Phase 1 can add en-US without migration.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form per-doc context — referenced master IDs, "
        "parent service slug, etc. Surfaces in ChromaDB metadata.",
    )
    checksum = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 of `content` (hex). K4 ingester compares "
        "to skip embedding unchanged content. Auto-computed on save "
        "via :meth:`compute_checksum`.",
    )
    content = models.TextField(
        help_text="Full source text. Chunks (K3) rebuilt from this on every ingest run.",
    )
    embedded_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="NULL = needs embedding. K4 ingester selects "
        "`embedded_at IS NULL OR embedded_at < updated_at`.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Default manager scopes to current_tenant(); admin / ingester use
    # ``all_tenants`` when crossing the boundary (e.g. nightly sweep).
    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "KB document"
        verbose_name_plural = "KB documents"
        ordering = ["-updated_at"]
        unique_together = (("tenant", "source_uri", "version"),)
        indexes = [
            # K4 ingester hot path: type + recency filter inside a tenant.
            models.Index(fields=["tenant", "doc_type", "-updated_at"]),
            # K8 legacy migration sweep selects by source_uri only.
            models.Index(fields=["tenant", "source_uri"]),
        ]

    def __str__(self) -> str:
        return f"KbDocument[{self.doc_type}:{self.source_uri}@v{self.version}]"

    @staticmethod
    def compute_checksum(content: str) -> str:
        """SHA-256 hex digest of UTF-8 content. K4 calls before embed."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def save(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        # Auto-fill checksum unless caller pre-computed (allows tests to
        # construct invalid-checksum rows for the K4 mismatch path).
        if not self.checksum and self.content:
            self.checksum = self.compute_checksum(self.content)
        super().save(*args, **kwargs)  # type: ignore[arg-type]
