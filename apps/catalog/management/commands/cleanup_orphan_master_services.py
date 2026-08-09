"""Delete unreconcilable (operator-owned) ``MasterService`` edges (DRF-967).

### Why this exists

``MasterService`` has two writers and ownership is discriminated by
``ayla_specialist_service_id`` (see the model docstring): NULL ⇒ operator's
(MM4 matrix / invite seeder / dev seed), non-NULL ⇒ catalog sync's. Sync only
ever touches rows it created, so a bad batch of NULL-provenance rows is
**immortal**: no sync beat can ever reconcile it away.

On the pilot tenant that immortality produced a cartesian product — every
master linked to every service in the catalog. Service-specific discovery then
degenerates: filtering by service returns every master, so the caller can't
resolve one service, and booking never gets a ``service_id``.

This command is the only supported way out. It deletes NULL-provenance edges
for one tenant so that the next catalog sync can re-create the real ones from
Ayla's ``specialist-services`` snapshot — this time sync-owned, hence
reconcilable forever after.

### Safety contract

* **Dry-run by default.** ``--apply`` is required to delete anything.
* **Sync-owned rows are never touched.** The delete is filtered on
  ``ayla_specialist_service_id IS NULL`` and the count of non-NULL rows is
  asserted unchanged afterwards.
* **``--apply`` requires ``--dump PATH``** and the dump is written and closed
  *before* the delete runs. The dump carries everything needed to re-insert
  the rows if the reconciliation goes wrong.
* The report names every master that ends the run with **zero** edges. Those
  masters are unbookable until the follow-up sync lands — that is the number
  to weigh before approving a run against a live tenant.

Usage::

    python manage.py cleanup_orphan_master_services --tenant-slug formula-tela
    python manage.py cleanup_orphan_master_services --tenant-id b32a057a-… \
        --apply --dump /var/backups/ms-orphans.json
"""

from __future__ import annotations

import json
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.catalog.models import MasterService
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = (
        "Delete MasterService rows with NULL ayla_specialist_service_id for one "
        "tenant (dry-run by default) so catalog sync can rebuild the real edges."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--tenant-slug",
            default=None,
            help="Tenant to clean, by slug. Mutually exclusive with --tenant-id.",
        )
        parser.add_argument(
            "--tenant-id",
            default=None,
            help="Tenant to clean, by UUID. Mutually exclusive with --tenant-slug.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete the rows (default: dry-run, report only). Requires --dump.",
        )
        parser.add_argument(
            "--dump",
            default=None,
            metavar="PATH",
            help="Write the affected rows to this JSON file. Mandatory with --apply; "
            "written and closed before anything is deleted.",
        )

    def handle(self, *args, **options) -> None:
        apply: bool = options["apply"]
        dump_path: str | None = options["dump"]

        tenant = self._resolve_tenant(options)

        if apply and not dump_path:
            raise CommandError(
                "--apply requires --dump PATH — the deleted rows must be recoverable."
            )

        all_edges = MasterService.all_tenants.filter(tenant=tenant)
        orphans = all_edges.filter(ayla_specialist_service_id__isnull=True)
        sync_owned_before = all_edges.filter(ayla_specialist_service_id__isnull=False).count()

        rows = self._serialise(orphans)
        self._report(tenant, rows=rows, total=all_edges.count(), sync_owned=sync_owned_before)

        if not rows:
            self.stdout.write(self.style.SUCCESS("nothing to clean — no NULL-provenance edges"))
            return

        if dump_path:
            self._write_dump(dump_path, tenant=tenant, rows=rows, applied=apply)
            self.stdout.write(f"dump written: {dump_path} ({len(rows)} rows)")

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY-RUN — nothing deleted. Re-run with --apply --dump PATH to "
                    f"remove {len(rows)} rows."
                )
            )
            return

        with transaction.atomic():
            deleted, _ = MasterService.all_tenants.filter(
                tenant=tenant, ayla_specialist_service_id__isnull=True
            ).delete()
            sync_owned_after = MasterService.all_tenants.filter(
                tenant=tenant, ayla_specialist_service_id__isnull=False
            ).count()
            if sync_owned_after != sync_owned_before:
                # Cannot happen through the filter above — assert it anyway, and
                # roll back if it ever does. Sync-owned rows are Ayla's mirror;
                # losing them silently would be far worse than this command
                # failing loudly.
                raise CommandError(
                    "ABORTED: sync-owned edge count changed "
                    f"({sync_owned_before} → {sync_owned_after}) — rolled back."
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"deleted {deleted} NULL-provenance edges; {sync_owned_after} sync-owned "
                "edges untouched. Run the catalog sync now to rebuild the real edges."
            )
        )

    # -- helpers ---------------------------------------------------------

    def _resolve_tenant(self, options: dict[str, Any]) -> Tenant:
        slug, tenant_id = options["tenant_slug"], options["tenant_id"]
        if bool(slug) == bool(tenant_id):
            raise CommandError("pass exactly one of --tenant-slug / --tenant-id")
        lookup = {"slug": slug} if slug else {"id": tenant_id}
        try:
            return Tenant.objects.get(**lookup)
        except (Tenant.DoesNotExist, ValueError, ValidationError) as exc:
            raise CommandError(f"tenant not found ({lookup})") from exc

    def _serialise(self, queryset) -> list[dict[str, Any]]:
        """Row dicts complete enough to re-insert on rollback."""
        return [
            {
                "id": str(row.id),
                "tenant_id": str(row.tenant_id),
                "master_id": str(row.master_id),
                "master_name": row.master.name,
                "service_id": str(row.service_id),
                "service_name": row.service.name,
                "created_by_id": row.created_by_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "ayla_specialist_service_id": None,
            }
            for row in queryset.select_related("master", "service").order_by(
                "master__name", "service__name"
            )
        ]

    def _report(
        self, tenant: Tenant, *, rows: list[dict[str, Any]], total: int, sync_owned: int
    ) -> None:
        self.stdout.write(
            f"tenant={tenant.slug} ({tenant.id})\n"
            f"  MasterService total     : {total}\n"
            f"  sync-owned (non-NULL)   : {sync_owned}  (never touched)\n"
            f"  orphan     (NULL)       : {len(rows)}  (removal candidates)"
        )
        if not rows:
            return

        removed_per_master: dict[str, int] = {}
        for row in rows:
            removed_per_master[row["master_name"]] = (
                removed_per_master.get(row["master_name"], 0) + 1
            )

        kept_per_master = dict(
            MasterService.all_tenants.filter(
                tenant=tenant, ayla_specialist_service_id__isnull=False
            )
            .values_list("master__name")
            .annotate(n=Count("id"))
        )

        self.stdout.write(f"\n{'master':<32} {'remove':>7} {'keep':>6}")
        self.stdout.write("-" * 48)
        stranded = []
        for name in sorted(removed_per_master):
            keep = kept_per_master.get(name, 0)
            self.stdout.write(f"{name:<32} {removed_per_master[name]:>7} {keep:>6}")
            if keep == 0:
                stranded.append(name)
        if stranded:
            self.stdout.write(
                self.style.WARNING(
                    f"\n{len(stranded)} master(s) will have ZERO edges until the next "
                    f"catalog sync rebuilds them — unbookable meanwhile: " + ", ".join(stranded)
                )
            )

    def _write_dump(
        self, path: str, *, tenant: Tenant, rows: list[dict[str, Any]], applied: bool
    ) -> None:
        payload = {
            "generated_at": timezone.now().isoformat(),
            "command": "cleanup_orphan_master_services",
            "ticket": "DRF-967",
            "tenant_id": str(tenant.id),
            "tenant_slug": tenant.slug,
            "mode": "apply" if applied else "dry-run",
            "row_count": len(rows),
            "rows": rows,
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
        except OSError as exc:
            raise CommandError(f"--dump unwritable: {exc}") from exc
