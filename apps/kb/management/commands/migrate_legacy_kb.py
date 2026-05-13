"""Legacy chromadb → KbDocument migration command (DRF-566 / Sprint 7 / K8).

One-shot bootstrap migration. The pre-platform legacy stack stored
HelpArticle embeddings in a file-backed ChromaDB at
``legacy_formulatela_mcp/data/chroma_db/``. Sprint 7 ai-bot-platform
holds KB content as :class:`apps.kb.models.KbDocument` rows that the
K4 ingester re-embeds into a tenant-scoped collection.

This command bridges the two:

1. Reads question + answer + legacy_id from a JSON manifest file
   (operator exports from mysite via a one-time script).
2. Builds the KB body in the same shape the projector (K7) uses for
   help articles: ``"<question>\\n<answer>"``.
3. Idempotent insert into :class:`KbDocument`. Re-runs are no-op when
   the checksum matches.
4. Counts inserted vs unchanged, estimates re-embed cost from token
   count × OpenAI text-embedding-3-small pricing.

### Why JSON not legacy chromadb directly

Risk #2 in the plan flagged: legacy uses embedded chromadb persistence
(SQLite-on-disk); platform uses HTTP-server persistence. File-copying
WOULD corrupt the format. We deliberately re-embed from the source
text — JSON manifest is the cleanest hand-off because:

* Operator already has the data in mysite Postgres (services_app.HelpArticle)
* Exporting `python manage.py dumpdata services_app.HelpArticle` produces a
  Django-fixture-style JSON
* The command reads that JSON; no legacy package import needed

### Idempotency

Each row gets ``source_uri = "mysite://help-articles/<legacy_id>"``.
Matching ``(tenant, source_uri, version=1)`` is `KbDocument.Meta.unique_together`,
so re-runs land in the same row and the K4 checksum guard skips
re-embedding when content is identical.

### Usage

::

    python manage.py migrate_legacy_kb \\
        --tenant formula-tela \\
        --source data/legacy_help_articles.json
    python manage.py migrate_legacy_kb \\
        --tenant formula-tela \\
        --source data/legacy_help_articles.json --dry-run

Exit codes:
  * 0 — success
  * 1 — tenant not found / source file missing / malformed JSON
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.kb.models import KbDocType, KbDocument
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# OpenAI text-embedding-3-small price (as of 2026-05): $0.020 / 1M tokens.
# Used for the cost-estimate log line — order-of-magnitude, not invoice-grade.
_PRICE_PER_MILLION_TOKENS = 0.020


class Command(BaseCommand):
    help = (
        "Migrate legacy HelpArticle records (JSON manifest) into "
        "KbDocument rows. Idempotent; re-runs skip unchanged rows."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--tenant",
            required=True,
            help="Tenant slug to import under (e.g. formula-tela).",
        )
        parser.add_argument(
            "--source",
            required=True,
            help="Path to JSON manifest. Either a Django-fixture-style list "
            "(`[{model: services_app.helparticle, pk: <id>, fields: "
            "{question: ..., answer: ...}}, ...]`) OR a plain list of "
            "`{id, question, answer}` objects.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse + count without writing any KbDocument rows.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_slug = options["tenant"]
        source_path = Path(options["source"])
        dry_run = bool(options["dry_run"])

        tenant = self._resolve_tenant(tenant_slug)
        records = self._load_manifest(source_path)

        created = 0
        unchanged = 0
        total_chars = 0

        for record in records:
            legacy_id = record["id"]
            question = (record.get("question") or "").strip()
            answer = (record.get("answer") or "").strip()
            if not question and not answer:
                continue
            body = f"{question}\n{answer}".strip()
            total_chars += len(body)

            source_uri = f"mysite://help-articles/{legacy_id}"
            new_checksum = KbDocument.compute_checksum(body)

            existing = KbDocument.all_tenants.filter(
                tenant=tenant,
                source_uri=source_uri,
                version=1,
            ).first()

            if existing is not None and existing.checksum == new_checksum:
                unchanged += 1
                continue

            if dry_run:
                created += 1
                continue

            if existing is None:
                KbDocument.all_tenants.create(
                    tenant=tenant,
                    doc_type=KbDocType.HELP_ARTICLE,
                    source_uri=source_uri,
                    version=1,
                    content=body,
                    checksum=new_checksum,
                    metadata={"legacy_import": True, "legacy_id": legacy_id},
                )
            else:
                KbDocument.all_tenants.filter(pk=existing.pk).update(
                    content=body,
                    checksum=new_checksum,
                    metadata={
                        **(existing.metadata or {}),
                        "legacy_import": True,
                        "legacy_id": legacy_id,
                    },
                    embedded_at=None,  # re-embed on next K6 sweep
                )
            created += 1

        est_tokens = total_chars / 4  # rough chars-per-token for ru/en mix
        est_cost = est_tokens / 1_000_000 * _PRICE_PER_MILLION_TOKENS

        verb = "would import" if dry_run else "imported"
        self.stdout.write(
            f"K8 legacy_kb: {verb} {created} doc(s), "
            f"{unchanged} unchanged (tenant={tenant_slug}). "
            f"Estimated re-embed cost: ~${est_cost:.4f} "
            f"({int(est_tokens):,} tokens at $0.020/1M)."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_tenant(self, slug: str) -> Tenant:
        try:
            return Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"tenant slug not found: {slug!r}") from exc

    def _load_manifest(self, source_path: Path) -> list[dict[str, Any]]:
        if not source_path.exists():
            raise CommandError(f"source manifest not found: {source_path}")

        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"malformed JSON in {source_path}: {exc}") from exc

        if not isinstance(payload, list):
            raise CommandError(f"manifest must be a JSON list; got {type(payload).__name__}")

        # Two accepted shapes:
        # (a) plain: [{"id": 1, "question": "...", "answer": "..."}, ...]
        # (b) Django fixture: [{"model": "services_app.helparticle",
        #                       "pk": 1, "fields": {"question": ..., "answer": ...}}, ...]
        records: list[dict[str, Any]] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            if "fields" in raw and "pk" in raw:
                fields = raw.get("fields", {})
                records.append(
                    {
                        "id": raw["pk"],
                        "question": fields.get("question", ""),
                        "answer": fields.get("answer", ""),
                    }
                )
            else:
                records.append(
                    {
                        "id": raw.get("id"),
                        "question": raw.get("question", ""),
                        "answer": raw.get("answer", ""),
                    }
                )
        return records
