"""Seed KbDocument rows from a mysite content dump.

Closes F5 (KB content pipe). Legacy mysite Postgres carries content
the salon already authored — service descriptions (50/60 rows, up to
3000 chars each), master bios (2 rows, up to 2360 chars), the
SiteSettings row (phone / email / address / hours), and active
promotions (10 rows). This command lands all of that as
:class:`apps.kb.models.KbDocument` rows in one idempotent pass, no
copywriter blocker.

### Why JSON manifest, not direct Postgres dial

Same reasoning as :mod:`migrate_legacy_kb` (DRF-566 / Sprint 7 / K8):

* Couples-of-convenience are expensive — platform code reading mysite
  schema breaks every time mysite drifts.
* Cross-DB credentials in platform env is a security smell.
* Two-step (export JSON → copy → import) keeps the trust boundary
  clean and gives operators a checkpointable artefact.

The operator runs the sibling export script on the mysite host
(``tools/mysite_export_kb.py``, shipped in the same PR) which dumps
to a single JSON file. Copy to the platform host. Run this command
pointing at the file.

### Manifest shape

::

    {
      "services": [
        {"id": 42, "name": "...", "description": "...", "category": "...",
         "duration_minutes": 60, "price": 3500},
        ...
      ],
      "masters": [
        {"id": 1, "name": "...", "bio": "..."},
        ...
      ],
      "site_settings": {
        "salon_name": "Формула тела",
        "phone": "8 (8412) 39-34-33",
        "email": "...",
        "address": "...",
        "working_hours": "Ежедневно с 9.00 до 21.00",
        "cancellation_policy": "" /* maybe empty */
      },
      "promotions": [
        {"id": 1, "name": "...", "description": "..."},
        ...
      ]
    }

Every section is optional. Empty / missing sections are skipped silently;
operators can run partial imports (e.g. site_settings only) without
producing a giant dump.

### Idempotency

Each row gets a stable ``source_uri``:

* Services         → ``mysite://services/<id>``           (doc_type=SERVICE)
* Masters          → ``mysite://masters/<id>``            (doc_type=MASTER)
* SiteSettings     → ``mysite://site/<field>``            (doc_type=FAQ for
                     contact info, doc_type=LEGAL for policy text)
* Promotions       → ``mysite://promotions/<id>``         (doc_type=FAQ)

``(tenant, source_uri, version=1)`` is the K1 unique constraint. Re-runs
land in the same rows; the K4 checksum guard skips re-embedding when
content is byte-identical. Content drift forces a re-embed by nulling
``embedded_at`` (K4 sweep picks it up).

### Usage

::

    python manage.py seed_kb_from_mysite \\
        --tenant formula-tela \\
        --source data/mysite_kb_export.json

    python manage.py seed_kb_from_mysite \\
        --tenant formula-tela \\
        --source data/mysite_kb_export.json --dry-run

Exit codes:
  * 0 — success
  * 1 — tenant not found / source missing / malformed JSON / unknown section
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


# OpenAI text-embedding-3-small price (2026-05). Order-of-magnitude only.
_PRICE_PER_MILLION_TOKENS = 0.020


# Mapping of SiteSettings field → (question prefix, doc_type). The
# prefix turns a bare value ("Пенза, Ул. Пушкина, 45") into a
# self-contained Q-A pair the retriever can rank against ("Где
# находится салон?") — RAG works much better when the chunk reads
# like an answer rather than a fragment.
_SITE_SETTING_DOCS: dict[str, tuple[str, KbDocType]] = {
    "address": ("Адрес салона:", KbDocType.FAQ),
    "phone": ("Телефон салона:", KbDocType.FAQ),
    "email": ("Email салона:", KbDocType.FAQ),
    "working_hours": ("Часы работы салона:", KbDocType.FAQ),
    "cancellation_policy": (
        "Политика отмены и переноса записи:",
        KbDocType.LEGAL,
    ),
    "privacy_policy": ("Политика конфиденциальности:", KbDocType.LEGAL),
    "terms_of_service": ("Условия использования:", KbDocType.LEGAL),
    "description": ("О салоне:", KbDocType.FAQ),
}


class Command(BaseCommand):
    help = (
        "Seed KbDocument rows from a mysite JSON export. Idempotent; "
        "re-runs skip unchanged content via checksum."
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
            help="Path to JSON manifest produced by tools/mysite_export_kb.py.",
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
        manifest = self._load_manifest(source_path)

        stats = {
            "services": (0, 0),
            "masters": (0, 0),
            "site_settings": (0, 0),
            "promotions": (0, 0),
        }
        total_chars = 0

        # Services — the bulk of content. Question prefix sets up RAG
        # retrieval: "что такое <name>" / "сколько стоит <name>" / etc.
        for record in manifest.get("services") or []:
            body = self._service_body(record)
            if body is None:
                continue
            total_chars += len(body)
            stats["services"] = self._upsert(
                tenant=tenant,
                source_uri=f"mysite://services/{record['id']}",
                doc_type=KbDocType.SERVICE,
                content=body,
                metadata={
                    "legacy_import": True,
                    "mysite_id": record["id"],
                    "name": record.get("name", ""),
                    "category": record.get("category", ""),
                    "duration_minutes": record.get("duration_minutes"),
                    "price": record.get("price"),
                },
                dry_run=dry_run,
                prev=stats["services"],
            )

        # Masters — bio + specialisation. Header is the master's name so
        # "кто работает с <category>" can match.
        for record in manifest.get("masters") or []:
            body = self._master_body(record)
            if body is None:
                continue
            total_chars += len(body)
            stats["masters"] = self._upsert(
                tenant=tenant,
                source_uri=f"mysite://masters/{record['id']}",
                doc_type=KbDocType.MASTER,
                content=body,
                metadata={
                    "legacy_import": True,
                    "mysite_id": record["id"],
                    "name": record.get("name", ""),
                },
                dry_run=dry_run,
                prev=stats["masters"],
            )

        # SiteSettings — one doc per non-empty field. Singleton row in
        # mysite, so source_uri keys off the field name.
        site = manifest.get("site_settings") or {}
        for field, (prefix, doc_type) in _SITE_SETTING_DOCS.items():
            value = (site.get(field) or "").strip()
            if not value:
                continue
            body = f"{prefix}\n{value}".strip()
            total_chars += len(body)
            stats["site_settings"] = self._upsert(
                tenant=tenant,
                source_uri=f"mysite://site/{field}",
                doc_type=doc_type,
                content=body,
                metadata={
                    "legacy_import": True,
                    "site_field": field,
                    "salon_name": site.get("salon_name", ""),
                },
                dry_run=dry_run,
                prev=stats["site_settings"],
            )

        # Promotions — current marketing copy. Treated as FAQ so the
        # retriever can surface them when a user asks "есть ли акции".
        for record in manifest.get("promotions") or []:
            body = self._promotion_body(record)
            if body is None:
                continue
            total_chars += len(body)
            stats["promotions"] = self._upsert(
                tenant=tenant,
                source_uri=f"mysite://promotions/{record['id']}",
                doc_type=KbDocType.FAQ,
                content=body,
                metadata={
                    "legacy_import": True,
                    "mysite_id": record["id"],
                    "name": record.get("name", ""),
                    "kind": "promotion",
                },
                dry_run=dry_run,
                prev=stats["promotions"],
            )

        est_tokens = total_chars / 4
        est_cost = est_tokens / 1_000_000 * _PRICE_PER_MILLION_TOKENS
        verb = "would import" if dry_run else "imported"

        total_created = sum(s[0] for s in stats.values())
        total_unchanged = sum(s[1] for s in stats.values())

        self.stdout.write(
            f"F5 seed_kb_from_mysite: {verb} {total_created} doc(s), "
            f"{total_unchanged} unchanged (tenant={tenant_slug})."
        )
        for section, (created, unchanged) in stats.items():
            if created or unchanged:
                self.stdout.write(f"  {section}: {created} {verb}, {unchanged} unchanged")
        self.stdout.write(
            f"  Estimated re-embed cost: ~${est_cost:.4f} "
            f"({int(est_tokens):,} tokens at $0.020/1M)."
        )

    # ------------------------------------------------------------------
    # Body shapers
    # ------------------------------------------------------------------

    @staticmethod
    def _service_body(record: dict[str, Any]) -> str | None:
        name = (record.get("name") or "").strip()
        description = (record.get("description") or "").strip()
        if not name or not description:
            return None
        # Lead with name so retriever's keyword overlap fires; follow
        # with descriptive prose for chunk diversity.
        return f"Услуга: {name}\n\n{description}"

    @staticmethod
    def _master_body(record: dict[str, Any]) -> str | None:
        name = (record.get("name") or "").strip()
        bio = (record.get("bio") or "").strip()
        if not name or not bio:
            return None
        return f"Мастер: {name}\n\n{bio}"

    @staticmethod
    def _promotion_body(record: dict[str, Any]) -> str | None:
        name = (record.get("name") or "").strip()
        description = (record.get("description") or "").strip()
        if not name:
            return None
        if description:
            return f"Акция: {name}\n\n{description}"
        return f"Акция: {name}"

    # ------------------------------------------------------------------
    # Idempotent upsert
    # ------------------------------------------------------------------

    @staticmethod
    def _upsert(
        *,
        tenant: Tenant,
        source_uri: str,
        doc_type: KbDocType,
        content: str,
        metadata: dict[str, Any],
        dry_run: bool,
        prev: tuple[int, int],
    ) -> tuple[int, int]:
        """Insert-or-update a KbDocument. Returns updated (created, unchanged) tuple."""
        created, unchanged = prev
        new_checksum = KbDocument.compute_checksum(content)

        existing = KbDocument.all_tenants.filter(
            tenant=tenant,
            source_uri=source_uri,
            version=1,
        ).first()

        if existing is not None and existing.checksum == new_checksum:
            return (created, unchanged + 1)

        if dry_run:
            return (created + 1, unchanged)

        if existing is None:
            KbDocument.all_tenants.create(
                tenant=tenant,
                doc_type=doc_type,
                source_uri=source_uri,
                version=1,
                content=content,
                checksum=new_checksum,
                metadata=metadata,
            )
        else:
            # Re-embed required: null embedded_at so the K4 sweep
            # picks it up on the next pass. Merge metadata so any
            # operator-set keys (e.g. manual review flags) survive.
            KbDocument.all_tenants.filter(pk=existing.pk).update(
                doc_type=doc_type,
                content=content,
                checksum=new_checksum,
                metadata={**(existing.metadata or {}), **metadata},
                embedded_at=None,
            )
        return (created + 1, unchanged)

    # ------------------------------------------------------------------
    # IO
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_tenant(slug: str) -> Tenant:
        try:
            return Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"tenant slug not found: {slug!r}") from exc

    @staticmethod
    def _load_manifest(source_path: Path) -> dict[str, Any]:
        if not source_path.exists():
            raise CommandError(f"source manifest not found: {source_path}")
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"malformed JSON in {source_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise CommandError(
                f"manifest root must be a JSON object with section keys; "
                f"got {type(payload).__name__}"
            )
        return payload
