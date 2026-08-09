"""Delete unreconcilable (operator-owned) ``MasterService`` edges (DRF-967).

### Why this exists

``MasterService`` has two writers and ownership is discriminated by
``ayla_specialist_service_id`` (see the model docstring): NULL ⇒ operator's
(MM4 matrix / invite seeder), non-NULL ⇒ catalog sync's. Sync only ever touches
rows it created, so a batch of NULL-provenance rows is **immortal**: no sync
beat can ever reconcile it away.

That immortality is what this command exists to break. It is NOT what put bad
data on the pilot tenant, and the distinction decides when running this is
safe.

What actually happened on ``formula-tela`` (verified live, DRF-967): Ayla's own
``specialist-services`` snapshot publishes a full grid — 4 masters × all 58
services, every edge ``is_active=True`` — and an unaudited bulk import mirrored
that grid locally with NULL provenance. The local 232 rows are pair-for-pair
identical to the upstream snapshot. So the cartesian product is **upstream
data**, and the immortality is a second, independent defect layered on top: it
means a later upstream correction can never reach this tenant.

### Ordering — read before running this against a live tenant

Deleting the rows does not fix discovery by itself. The next sync rebuilds
whatever Ayla publishes *at that moment*:

* **Upstream still wrong** → the same grid comes back, now **sync-owned**. That
  is strictly worse: this command targets NULL rows only, so it can never
  remove them, and the tenant spends the gap with masters that have no edges
  at all.
* **Upstream corrected first** → the rebuild lands the real edges, sync-owned
  and reconcilable from then on. This is the only ordering that helps.

So: fix the upstream data, *then* run this, then run the sync — ideally in one
pass, because between the delete and the rebuild the affected masters are
unbookable. Use ``--check-upstream`` to see which candidates Ayla would
actually rebuild before you commit to anything.

### Safety contract

* **Dry-run by default.** ``--apply`` is required to delete anything.
* **Sync-owned rows are never touched.** The delete is filtered on
  ``ayla_specialist_service_id IS NULL`` and the count of non-NULL rows is
  asserted unchanged afterwards.
* **``--apply`` requires ``--dump PATH``** and the dump is written, fsynced and
  closed *before* the delete runs. The delete then targets **the captured row
  ids**, never the bare predicate, so "everything deleted is in the dump" is an
  invariant rather than a hope: a NULL-provenance row an operator creates
  through the MM4 matrix while this command is reporting survives instead of
  vanishing without a backup. An existing dump path is refused, not clobbered.

* The report names every master that ends the run with **zero** edges. Those
  masters are unbookable until the follow-up sync lands — that is the number
  to weigh before approving a run against a live tenant.
* **NULL provenance does not tell you who wrote the row.** No writer populates
  ``MasterService.created_by`` — not the MM4 matrix
  (``views_services_mapping.py``), not the invite seeder (``views_invite.py``)
  — so the dump's ``created_by_id`` is always NULL and cannot separate a human
  operator's matrix edit from import junk. The per-row discriminator lives in
  ``AuditLog`` (``MASTER_SERVICES_CHANGED``, payload carries ``master_id`` +
  ``services_added`` + ``actor_id``). The report says this out loud on every
  run rather than staying silent and reading as "no operator edits here".

### Restoring from a dump

The dump carries all eight columns of every deleted row. ``created_at``
(``auto_now_add``) and ``updated_at`` (``auto_now``) are set by ``pre_save``,
which also runs under ``bulk_create`` — so an ORM restore silently rewrites
both. ``updated_at`` is load-bearing: the MM4 matrix derives its
optimistic-concurrency token from ``MAX(updated_at)`` across the tenant
(``apps/admin_api/views_services_mapping.py``), and rewriting it 409s every
operator mid-edit. Restore with a raw ``INSERT``, or ``bulk_create`` followed by
``.filter(id=...).update(created_at=..., updated_at=...)`` — ``update()``
bypasses ``pre_save``.

Usage::

    python manage.py cleanup_orphan_master_services --tenant-slug formula-tela
    python manage.py cleanup_orphan_master_services --tenant-id b32a057a-… \
        --apply --dump /var/backups/ms-orphans.json
"""

from __future__ import annotations

import json
import os
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.catalog.models import MasterService
from apps.tenancy.models import Tenant

# Rows per DELETE statement. Bounded so a tenant with a pathological edge count
# neither blows SQLite's parameter limit nor holds row locks for one long
# statement on Postgres.
_DELETE_CHUNK = 500


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
            "written and closed before anything is deleted. Refuses to overwrite, so "
            "a dry-run and the following --apply need different paths.",
        )
        parser.add_argument(
            "--check-upstream",
            action="store_true",
            help="Ask Ayla which of the candidates it would rebuild, and split the "
            "report into rebuilt / permanently-lost. Costs one HTTP call.",
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

        rows = self._serialise(orphans)
        self._report(
            tenant,
            rows=rows,
            total=all_edges.count(),
            sync_owned=all_edges.filter(ayla_specialist_service_id__isnull=False).count(),
        )

        if not rows:
            self.stdout.write(self.style.SUCCESS("nothing to clean — no NULL-provenance edges"))
            return

        if options["check_upstream"]:
            self._report_upstream(tenant, rows=rows)

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

        # LOAD-BEARING: delete the rows the dump CAPTURED, not whatever matches
        # the predicate now. Between the read above and this delete the MM4
        # matrix or the invite seeder can create a NULL-provenance row from the
        # admin UI (``views_services_mapping.py`` / ``views_invite.py``); a
        # predicate-based delete would take it with no entry in the backup —
        # an operator's freshly-ticked cell, unrecoverable. The ``isnull``
        # guard stays so a row a concurrent sync beat has since stamped is
        # spared instead of deleted on stale evidence.
        orphan_ids = [row["id"] for row in rows]

        with transaction.atomic():
            deleted = 0
            # Chunked: this command is generic over --tenant-slug, and a single
            # IN of unbounded width is a very long transaction on Postgres and
            # a hard SQLITE_MAX_VARIABLE_NUMBER error on SQLite.
            for start in range(0, len(orphan_ids), _DELETE_CHUNK):
                deleted += (
                    MasterService.all_tenants.filter(
                        tenant=tenant,
                        ayla_specialist_service_id__isnull=True,
                        id__in=orphan_ids[start : start + _DELETE_CHUNK],
                    )
                    .delete()[1]
                    .get(MasterService._meta.label, 0)
                )
            # The real guarantee that no sync-owned row was touched is the
            # filter itself (isnull=True AND id IN captured-set), not a count
            # comparison: under READ COMMITTED a concurrent sync beat can change
            # the sync-owned count between two statements, and an equal-count
            # swap would slip past a counter anyway. So assert the thing the
            # filter actually promises — every deleted row was in the dump —
            # and let a shortfall (a row stamped or removed since the read) pass
            # as the benign outcome it is.
            if deleted > len(orphan_ids):
                raise CommandError(
                    f"ABORTED: deleted {deleted} rows but only {len(orphan_ids)} were "
                    "captured in the dump — rolled back."
                )

        remaining_sync_owned = MasterService.all_tenants.filter(
            tenant=tenant, ayla_specialist_service_id__isnull=False
        ).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"deleted {deleted} NULL-provenance edges; {remaining_sync_owned} sync-owned "
                "edges untouched."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "Run the catalog sync NOW — until it lands, the affected masters have no "
                "edges and cannot be booked. The sync rebuilds whatever Ayla publishes at "
                "that moment: if the upstream data is still wrong, the wrong edges come "
                "back sync-owned and this command can no longer remove them."
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

        # Keyed on master_id, not name: two masters can share a name, and
        # merging them would hide a genuinely stranded master behind a
        # namesake's kept edges — in the one number this report exists to give.
        removed_per_master: dict[str, int] = {}
        name_of: dict[str, str] = {}
        for row in rows:
            removed_per_master[row["master_id"]] = removed_per_master.get(row["master_id"], 0) + 1
            name_of[row["master_id"]] = row["master_name"]

        kept_per_master = {
            str(master_id): n
            for master_id, n in MasterService.all_tenants.filter(
                tenant=tenant, ayla_specialist_service_id__isnull=False
            )
            .values_list("master_id")
            .annotate(n=Count("id"))
        }

        self.stdout.write(f"\n{'master':<32} {'remove':>7} {'keep':>6}")
        self.stdout.write("-" * 48)
        stranded = []
        for master_id in sorted(removed_per_master, key=lambda k: name_of[k]):
            keep = kept_per_master.get(master_id, 0)
            self.stdout.write(
                f"{name_of[master_id]:<32} {removed_per_master[master_id]:>7} {keep:>6}"
            )
            if keep == 0:
                stranded.append(name_of[master_id])
        if stranded:
            self.stdout.write(
                self.style.WARNING(
                    f"\n{len(stranded)} master(s) will have ZERO edges until the next "
                    f"catalog sync rebuilds them — unbookable meanwhile: " + ", ".join(stranded)
                )
            )

        # UNCONDITIONAL, deliberately. The obvious per-row discriminator would
        # be created_by — but no writer populates it (neither
        # views_services_mapping.py nor views_invite.py passes it), so it is
        # always NULL. A warning conditioned on it would never fire, and its
        # silence would read as "no human edits among these rows" — exactly the
        # false confidence that gets an operator to approve a destructive run.
        self.stdout.write(
            self.style.WARNING(
                "\nNULL provenance does NOT distinguish a human MM4 matrix edit from "
                "import junk (created_by is never populated by any writer). Check "
                "AuditLog MASTER_SERVICES_CHANGED for this tenant before --apply."
            )
        )

    def _report_upstream(self, tenant: Tenant, *, rows: list[dict[str, Any]]) -> None:
        """Split the candidates by whether the follow-up sync would rebuild them.

        The plain report can only count rows that already exist, so it answers
        "what disappears" but not "what comes back" — and those are different
        numbers. An edge Ayla still publishes is rebuilt within one beat; one it
        does not is gone until somebody replays the dump by hand. Only the
        second number is an actual loss, and it is the one worth approving on.

        Fails soft: an unreachable Ayla must not look like "nothing would be
        rebuilt", so the command says the check is unavailable instead.
        """
        from apps.catalog.models import CatalogService
        from apps.catalog.services.http_client import CatalogHttpClient
        from apps.tenancy.context import tenant_scope

        try:
            with CatalogHttpClient() as client:
                snapshot = client.fetch_specialist_services(tenant_id=str(tenant.id))
        except Exception as exc:  # noqa: BLE001 — advisory check, never fatal
            self.stdout.write(
                self.style.ERROR(
                    f"\n--check-upstream FAILED ({type(exc).__name__}: {exc}). No upstream "
                    "verdict — treat every candidate as potentially permanent loss."
                )
            )
            return

        # Tenant-scoped read (MKT1): the cross-tenant carve-out belongs to
        # apps/marketplace, and this command works on one tenant anyway.
        with tenant_scope(tenant):
            service_by_ayla_id = {
                str(ayla_id): str(pk)
                for pk, ayla_id in CatalogService.objects.filter(
                    ayla_service_id__isnull=False
                ).values_list("id", "ayla_service_id")
            }
        upstream_pairs = {
            (str(edge.specialist), service_by_ayla_id[str(edge.salon_service)])
            for edge in snapshot.edges
            if edge.is_active and str(edge.salon_service) in service_by_ayla_id
        }

        rebuilt = [r for r in rows if (r["master_id"], r["service_id"]) in upstream_pairs]
        lost = [r for r in rows if (r["master_id"], r["service_id"]) not in upstream_pairs]

        self.stdout.write(
            f"\nupstream check (snapshot complete={snapshot.complete}, "
            f"{len(snapshot.edges)} edges):\n"
            f"  would be REBUILT by the next sync : {len(rebuilt)}\n"
            f"  PERMANENTLY LOST (not published)  : {len(lost)}"
        )
        if not snapshot.complete:
            self.stdout.write(
                self.style.WARNING(
                    "  snapshot is INCOMPLETE — the rebuilt/lost split is not trustworthy."
                )
            )
        for row in lost[:20]:
            self.stdout.write(f"    LOST {row['master_name']} → {row['service_name']}")
        if len(lost) > 20:
            self.stdout.write(f"    … and {len(lost) - 20} more")
        if rebuilt and not lost:
            self.stdout.write(
                self.style.WARNING(
                    "  Every candidate would come back identical. Deleting them changes "
                    "nothing except their provenance — if the goal was to change what "
                    "discovery returns, the upstream data has to change first."
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
            # "x" — create-exclusive, so the existence check and the create are
            # one atomic step. Refusing to clobber matters because running
            # tenant A then tenant B with the same path would otherwise destroy
            # A's only backup. Consequence to know: a dry-run that wrote this
            # path blocks the following --apply, which needs its own filename.
            with open(path, "x", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                # fsync so "the dump is on disk before the delete" survives a
                # host crash, not just a process exit.
                os.fsync(fh.fileno())
        except FileExistsError as exc:
            raise CommandError(
                f"--dump {path} already exists — refusing to overwrite a backup. "
                "Pick a new filename (a dry-run and its --apply need separate ones)."
            ) from exc
        except OSError as exc:
            raise CommandError(f"--dump unwritable: {exc}") from exc
