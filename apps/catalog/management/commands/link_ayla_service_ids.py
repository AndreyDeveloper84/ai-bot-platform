"""Back-fill ``CatalogService.ayla_service_id`` for legacy rows (S1-B).

Pilot gate for the ``BOOKING_VIA_AYLA_REST`` flip (#1016 / #1034): the
health-check gate grounds on ``ayla_service_id`` and fails closed on a
miss, so Penza pilot services must be ~100% linked before the flag can
go ON. This command fetches Ayla salon-services (the canonical catalog,
#1044) and links unlinked legacy rows by slug → normalized name
(:mod:`apps.catalog.services.linking`).

Dry-run by default — the report is the primary output. ``--apply``
writes; ``--deactivate-duplicates`` (requires ``--apply``) retires
legacy twins whose match already lives on an Ayla-keyed row.
``--fail-under PCT`` turns the command into a go/no-go gate (exit 2 via
``CommandError`` when projected coverage is below the threshold).

Usage::

    python manage.py link_ayla_service_ids --tenant-slug formula-tela
    python manage.py link_ayla_service_ids --tenant-slug formula-tela --apply
    python manage.py link_ayla_service_ids --apply --fail-under 100
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.services.http_client import CatalogError, CatalogHttpClient
from apps.catalog.services.linking import TenantLinkReport, link_tenant_services
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Back-fill CatalogService.ayla_service_id (dry-run by default) + coverage report."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--tenant-slug",
            default=None,
            help="Limit to a single tenant (default: all tenants).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the links (default: dry-run, report only).",
        )
        parser.add_argument(
            "--deactivate-duplicates",
            action="store_true",
            help="With --apply: retire legacy rows whose match already "
            "exists on an Ayla-keyed row (is_active=False).",
        )
        parser.add_argument(
            "--fail-under",
            type=float,
            default=None,
            metavar="PCT",
            help="Exit with an error if projected total coverage is below PCT.",
        )

    def handle(self, *args, **options) -> None:
        apply: bool = options["apply"]
        deactivate: bool = options["deactivate_duplicates"]
        fail_under: float | None = options["fail_under"]

        if deactivate and not apply:
            raise CommandError("--deactivate-duplicates requires --apply.")

        tenants = Tenant.objects.all().order_by("slug")
        if options["tenant_slug"]:
            tenants = tenants.filter(slug=options["tenant_slug"])
        if not tenants.exists():
            raise CommandError(f"no tenants matched (slug={options['tenant_slug']!r})")

        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(
            f"mode={mode} deactivate_duplicates={deactivate}\n"
            f"{'tenant':<28} {'linked':>7} {'dup':>4} {'unmatched':>10} "
            f"{'active':>7} {'cov->proj':>16}"
        )
        self.stdout.write("-" * 78)

        reports: list[TenantLinkReport] = []
        with CatalogHttpClient() as client:
            for tenant in tenants:
                try:
                    dtos = client.fetch_salon_services(tenant_id=str(tenant.id))
                except CatalogError as exc:
                    raise CommandError(
                        f"Ayla catalog fetch failed for {tenant.slug}: {exc}"
                    ) from exc
                report = link_tenant_services(
                    tenant,
                    dtos,
                    apply=apply,
                    deactivate_duplicates=deactivate,
                )
                reports.append(report)
                self._print_tenant(report)

        total_active = sum(r.active_before for r in reports)
        total_covered = sum(r.covered_before for r in reports)
        total_linked = sum(len(r.links) for r in reports)
        total_retired = sum(sum(1 for d in r.duplicates if d.deactivated) for r in reports)
        projected_active = total_active - total_retired
        projected = (total_covered + total_linked) / projected_active if projected_active else 1.0

        self.stdout.write("-" * 78)
        self.stdout.write(
            f"{'TOTAL':<28} {total_linked:>7} "
            f"{sum(len(r.duplicates) for r in reports):>4} "
            f"{sum(len(r.unmatched) for r in reports):>10} "
            f"{total_active:>7} "
            f"{self._pct(total_covered / total_active if total_active else 1.0)}->"
            f"{self._pct(projected):>6}"
        )
        self._print_details(reports)

        if fail_under is not None and projected * 100 < fail_under:
            raise CommandError(
                f"coverage {projected * 100:.1f}% below threshold {fail_under:.1f}% — "
                "see unmatched/duplicate detail above; do NOT flip BOOKING_VIA_AYLA_REST."
            )
        self.stdout.write(self.style.SUCCESS(f"projected coverage: {projected * 100:.1f}%"))

    def _print_tenant(self, report: TenantLinkReport) -> None:
        before = report.covered_before / report.active_before if report.active_before else 1.0
        self.stdout.write(
            f"{report.tenant_slug:<28} {len(report.links):>7} {len(report.duplicates):>4} "
            f"{len(report.unmatched):>10} {report.active_before:>7} "
            f"{self._pct(before)}->{self._pct(report.coverage_after):>6}"
        )

    def _print_details(self, reports: list[TenantLinkReport]) -> None:
        for report in reports:
            for dup in report.duplicates:
                self.stdout.write(
                    self.style.WARNING(
                        f"DUPLICATE {report.tenant_slug} pk={dup.service_pk} "
                        f"slug={dup.slug!r} -> ayla {dup.ayla_service_id} already on "
                        f"pk={dup.existing_pk}" + (" (deactivated)" if dup.deactivated else "")
                    )
                )
            for row in report.unmatched:
                self.stdout.write(
                    f"UNMATCHED {report.tenant_slug} pk={row.service_pk} "
                    f"slug={row.slug!r} name={row.name!r}"
                )

    @staticmethod
    def _pct(value: float) -> str:
        return f"{value * 100:.1f}%"
