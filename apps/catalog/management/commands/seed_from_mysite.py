"""Pull the salon catalog + descriptions from mysite_stage into the platform.

One-off bootstrap for dev cutover: read mysite's ``services_app_service``
rows over a direct Postgres connection, upsert them into
:class:`apps.catalog.models.CatalogService` AND create matching
:class:`apps.kb.models.KbDocument` rows so the FAQ skill's RAG retriever
has chunks to ground answers in.

For prod the platform-side ``apps.catalog.tasks.sync_catalog_for_all_tenants``
beat owns this via HTTP (M1 DRF-592). That requires a public mysite API
+ service-token plumbing that the dev mysite doesn't ship; this command
is the no-credentials shortcut for the dev environment.

Usage::

    python manage.py seed_from_mysite --tenant-slug formula-tela \\
      --mysite-db mysite_stage --mysite-user mysite_stage_user

Env vars (alternative to flags): ``MYSITE_DB_HOST``, ``MYSITE_DB_PORT``,
``MYSITE_DB_NAME``, ``MYSITE_DB_USER``, ``MYSITE_DB_PASSWORD``.
"""

from __future__ import annotations

import hashlib
import os

import psycopg  # type: ignore[import-untyped]
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone as django_tz

from apps.catalog.models import CatalogService
from apps.kb.models import KbDocType, KbDocument
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Pull services from mysite_stage and seed CatalogService + KbDocument rows."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant-slug", required=True)
        parser.add_argument("--mysite-host", default=os.environ.get("MYSITE_DB_HOST", "127.0.0.1"))
        parser.add_argument("--mysite-port", default=os.environ.get("MYSITE_DB_PORT", "5432"))
        parser.add_argument("--mysite-db", default=os.environ.get("MYSITE_DB_NAME", "mysite_stage"))
        parser.add_argument(
            "--mysite-user",
            default=os.environ.get("MYSITE_DB_USER", "mysite_stage_user"),
        )
        parser.add_argument(
            "--mysite-password",
            default=os.environ.get("MYSITE_DB_PASSWORD", ""),
            help="Defaults to MYSITE_DB_PASSWORD env. Pass --mysite-password '' to force prompt.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(
        self,
        *,
        tenant_slug: str,
        mysite_host: str,
        mysite_port: str,
        mysite_db: str,
        mysite_user: str,
        mysite_password: str,
        dry_run: bool,
        **opts,
    ) -> None:
        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"Tenant {tenant_slug!r} not found.") from exc

        if not mysite_password:
            raise CommandError(
                "mysite DB password missing. Pass --mysite-password or set MYSITE_DB_PASSWORD."
            )

        conn_kwargs = {
            "host": mysite_host,
            "port": int(mysite_port),
            "dbname": mysite_db,
            "user": mysite_user,
            "password": mysite_password,
        }
        self.stdout.write(
            f"connecting to mysite postgres at {mysite_host}:{mysite_port}/{mysite_db}"
        )
        with psycopg.connect(**conn_kwargs) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, short_description, description, price, price_from,
                       duration, duration_min, is_active, is_popular, slug, updated_at
                FROM services_app_service
                WHERE is_active = true
                ORDER BY id
                """,
            )
            rows = cur.fetchall()

        self.stdout.write(f"fetched {len(rows)} active services from mysite")

        if dry_run:
            for row in rows[:5]:
                self.stdout.write(f"  preview: id={row[0]} name={row[1]!r} price={row[4]}")
            self.stdout.write(self.style.WARNING("dry-run — no writes"))
            return

        created_catalog = 0
        updated_catalog = 0
        created_kb = 0
        updated_kb = 0

        with tenant_scope(tenant):
            for row in rows:
                (
                    ext_id,
                    name,
                    short_desc,
                    description,
                    price,
                    price_from,
                    duration,
                    duration_min,
                    is_active,
                    is_popular,
                    slug,
                    updated_at,
                ) = row

                effective_price = price_from if price_from is not None else price
                effective_duration = duration_min if duration_min is not None else duration
                effective_updated_at = updated_at or django_tz.now()

                # CatalogService upsert
                obj, was_created = CatalogService.all_tenants.update_or_create(
                    tenant=tenant,
                    external_id=ext_id,
                    defaults={
                        "slug": slug or f"service-{ext_id}",
                        "name": name,
                        "short_description": short_desc or "",
                        "description": description or "",
                        "price_from": effective_price,
                        "duration_min": effective_duration,
                        "is_active": is_active,
                        "is_popular": is_popular,
                        "external_updated_at": effective_updated_at,
                    },
                )
                if was_created:
                    created_catalog += 1
                else:
                    updated_catalog += 1

                # KbDocument for FAQ retrieval. Use service description if
                # present, otherwise short_description, plus a header line
                # with price + duration so price-lookup queries hit the chunk.
                price_line = ""
                if effective_price:
                    price_line = f"Цена: от {int(effective_price)}₽."
                duration_line = (
                    f"Продолжительность процедуры: {effective_duration} мин."
                    if effective_duration
                    else ""
                )
                content_body = description or short_desc or name
                content = "\n\n".join(
                    line
                    for line in [
                        f"# {name}",
                        price_line,
                        duration_line,
                        content_body.strip(),
                    ]
                    if line
                )
                source_uri = f"mysite://services/{ext_id}"

                checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

                # Pull current latest version (if any) for this source_uri.
                latest = (
                    KbDocument.objects.filter(source_uri=source_uri).order_by("-version").first()
                )
                if latest is None:
                    KbDocument.objects.create(
                        tenant=tenant,
                        doc_type=KbDocType.SERVICE,
                        source_uri=source_uri,
                        version=1,
                        content=content,
                        checksum=checksum,
                        metadata={
                            "service_id": ext_id,
                            "service_name": name,
                            "price_rub": float(effective_price) if effective_price else None,
                            "duration_min": effective_duration,
                            "slug": slug or "",
                        },
                    )
                    created_kb += 1
                elif latest.checksum == checksum:
                    # Content unchanged — no-op.
                    pass
                else:
                    KbDocument.objects.create(
                        tenant=tenant,
                        doc_type=KbDocType.SERVICE,
                        source_uri=source_uri,
                        version=latest.version + 1,
                        content=content,
                        checksum=checksum,
                        metadata={
                            "service_id": ext_id,
                            "service_name": name,
                            "price_rub": float(effective_price) if effective_price else None,
                            "duration_min": effective_duration,
                            "slug": slug or "",
                        },
                    )
                    updated_kb += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"CatalogService: +{created_catalog} created, ~{updated_catalog} updated. "
                f"KbDocument: +{created_kb} created, ~{updated_kb} updated."
            )
        )
        self.stdout.write(
            "Next: trigger embedding via "
            "`celery -A config call apps.kb.tasks.embed_pending_kb_documents` or wait for beat."
        )
