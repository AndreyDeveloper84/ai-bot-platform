"""One-shot catalog sync + freshness status for operators (DRF-1494).

Before this command the only way the catalog sync could run was the Celery
beat. ``apps/catalog/services/sync.py`` claimed an admin "force resync"
action existed; it did not (C6/DRF-576 was never built). So when the pilot
mirror went stale there was no supported way to push a fix through without
waiting for a beat that was, by then, the thing that was broken.

Two modes, both explicit:

``--status``   Read-only. Prints, per tenant, when the catalog sync last
               completed and how many rows the mirror holds. Writes
               nothing, talks to nothing, and is the answer to "is it
               stale?" that does not require a psql prompt.

(default)      Runs :class:`~apps.catalog.services.sync.CatalogSyncService`
               over every tenant, or one with ``--tenant``, and prints the
               row counts before and after so the run's effect is a number
               rather than a claim.

``--dry-run``  Fetches from Ayla and reports what the upsert would change,
               without opening a write transaction.

Safety. The sync is upsert-only for services and masters: it creates and
updates rows, and never deletes one. The single destructive action in the
cycle is edge reconciliation (``MasterService`` rows absent from a
*complete* upstream snapshot), which is exactly the behaviour the beat has
every fifteen minutes — this command adds no authority the scheduled run
does not already have. Its blast radius is one tenant when ``--tenant`` is
given.

Usage::

    python manage.py sync_catalog --status
    python manage.py sync_catalog --tenant formula-tela --dry-run
    python manage.py sync_catalog --tenant formula-tela
    python manage.py sync_catalog

Exit codes:
  * 0 — every tenant attempted synced or skipped cleanly
  * 1 — at least one tenant failed (details on stderr)
  * 2 — bad args (CommandError)
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import CatalogService
from apps.catalog.services.sync import CatalogSyncService
from apps.catalog.staleness import sync_ages
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Run the Ayla catalog sync once, or report how stale each tenant's mirror is."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--tenant",
            dest="tenant_slug",
            default=None,
            help="Limit the run to one tenant slug. Omit to run every tenant.",
        )
        parser.add_argument(
            "--status",
            action="store_true",
            help="Read-only freshness report. No fetch, no writes.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and report, write nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["status"]:
            self._status()
            return

        tenants = self._select_tenants(options["tenant_slug"])
        if options["dry_run"]:
            self._dry_run(tenants)
            return

        self._run(tenants)

    # ------------------------------------------------------------------

    def _select_tenants(self, slug: str | None) -> list[Tenant]:
        if slug:
            try:
                return [Tenant.objects.get(slug=slug)]
            except Tenant.DoesNotExist as exc:
                raise CommandError(f"no tenant with slug {slug!r}") from exc
        # Same exclusion as the beat (#1019): the global_bot sentinel owns
        # global BotUsers and discovery, not a salon catalog, and the Ayla
        # fetch with its UUID answers 400 every time.
        return list(Tenant.objects.exclude(slug=GLOBAL_BOT_TENANT_SLUG).order_by("slug"))

    def _mirror_count(self, tenant: Tenant) -> int:
        return CatalogService.all_tenants.filter(tenant=tenant).count()

    def _status(self) -> None:
        self.stdout.write(f"{'tenant':<24} {'last sync ok':<28} {'age':>8}  {'mirrored':>8}")
        self.stdout.write("-" * 74)
        for age in sync_ages():
            tenant = Tenant.objects.get(slug=age.slug)
            marker = " STALE" if age.is_stale else ""
            last = age.last_ok_at.isoformat() if age.last_ok_at else "never"
            self.stdout.write(
                f"{age.slug:<24} {last:<28} {age.age_human:>8}  "
                f"{self._mirror_count(tenant):>8}{marker}"
            )

    def _dry_run(self, tenants: list[Tenant]) -> None:
        # Dry run reads the same surface the sync reads, so what it reports
        # is the fetch the sync would act on -- not a guess about it.
        from apps.catalog.services.http_client import CatalogHttpClient

        for tenant in tenants:
            before = self._mirror_count(tenant)
            try:
                with CatalogHttpClient() as http:
                    dtos = http.fetch_salon_services(tenant_id=str(tenant.id))
            except Exception as exc:  # noqa: BLE001 — operator-facing boundary
                self.stderr.write(f"{tenant.slug}: FETCH FAILED {exc.__class__.__name__}: {exc}")
                continue
            known = set(
                CatalogService.all_tenants.filter(tenant=tenant).values_list(
                    "ayla_service_id", flat=True
                )
            )
            incoming = {dto.ayla_service_id for dto in dtos}
            self.stdout.write(
                f"{tenant.slug}: upstream={len(dtos)} mirrored={before} "
                f"would_create={len(incoming - known)} would_update={len(incoming & known)} "
                f"(dry run — nothing written)"
            )

    def _run(self, tenants: list[Tenant]) -> None:
        service = CatalogSyncService()
        failures = 0
        for tenant in tenants:
            before = self._mirror_count(tenant)
            try:
                result = service.run(tenant)
            except Exception as exc:  # noqa: BLE001 — operator-facing boundary
                failures += 1
                self.stderr.write(f"{tenant.slug}: RAISED {exc.__class__.__name__}: {exc}")
                continue

            after = self._mirror_count(tenant)
            if result.skipped:
                self.stdout.write(f"{tenant.slug}: skipped (another run holds the lock)")
                continue
            if result.error:
                failures += 1
                self.stderr.write(f"{tenant.slug}: FAILED {result.error}")
                continue
            self.stdout.write(
                f"{tenant.slug}: services {before} -> {after} "
                f"(created={result.services.created} updated={result.services.updated}) "
                f"masters(created={result.masters.created} updated={result.masters.updated}) "
                f"edges(created={result.master_services.created} "
                f"removed={result.master_services.removed})"
            )

        if failures:
            raise SystemExit(1)
