"""Back-fill ``CatalogService.ayla_service_id`` for legacy rows (C6 / AMD-001).

Pilot gate for the ``BOOKING_VIA_AYLA_REST`` flip (#1016 / #1034): the
health-check gate grounds on ``ayla_service_id`` and fails closed on a
miss, so Penza pilot services must be ~100% linked before the flag can
go ON. Matching follows the C6 contract: pair ``(category_slug,
normalized name)`` + duration tiebreaker; normalization lower/trim/ё→е/
collapse spaces/strip «ёлочки» (:mod:`apps.catalog.services.linking`).

Dry-run by default — the report is the primary output. ``--apply``
writes; ``--deactivate-duplicates`` (requires ``--apply``) retires
legacy twins whose match already lives on an Ayla-keyed row.
``--mapping-file`` carries manual correspondences (JSON object:
``{"<ayla_service_id | template_id>": {"tenant_slug": ..., "service_slug":
...}}``) for rows that cannot auto-match. ``--fail-under PCT`` turns the
command into a go/no-go gate (exit 2 via ``CommandError`` when projected
coverage is below the threshold).

Coverage report buckets per tenant + total:
``matched auto`` (pair / pair+duration) · ``matched manual`` ·
``unmatched`` (detail-listed) · duplicates.

Usage::

    python manage.py link_ayla_service_ids --tenant-slug formula-tela
    python manage.py link_ayla_service_ids --tenant-slug formula-tela --apply
    python manage.py link_ayla_service_ids --apply --mapping-file mapping.json --fail-under 100
"""

from __future__ import annotations

import json

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
            "--mapping-file",
            default=None,
            metavar="PATH",
            help="JSON mapping file of manual correspondences: "
            '{"<ayla_service_id|template_id>": {"tenant_slug": ..., '
            '"service_slug": ...}}.',
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

        manual_map = self._load_mapping(options["mapping_file"])

        tenants = Tenant.objects.all().order_by("slug")
        if options["tenant_slug"]:
            tenants = tenants.filter(slug=options["tenant_slug"])
        if not tenants.exists():
            raise CommandError(f"no tenants matched (slug={options['tenant_slug']!r})")

        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(
            f"mode={mode} deactivate_duplicates={deactivate} manual_entries={len(manual_map)}\n"
            f"{'tenant':<22} {'auto':>5} {'manual':>7} {'dup':>4} "
            f"{'unmatched':>10} {'active':>7} {'cov->proj':>16}"
        )
        self.stdout.write("-" * 74)

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
                    manual_map=manual_map,
                )
                reports.append(report)
                self._print_tenant(report)

        totals = {
            "auto": sum(len(r.matched_auto) for r in reports),
            "manual": sum(len(r.matched_manual) for r in reports),
            "dup": sum(len(r.duplicates) for r in reports),
            "unmatched": sum(len(r.unmatched) for r in reports),
            "active": sum(r.active_before for r in reports),
        }
        covered_before = sum(r.covered_before for r in reports)
        retired = sum(sum(1 for d in r.duplicates if d.deactivated) for r in reports)
        projected_active = totals["active"] - retired
        projected = (
            (covered_before + totals["auto"] + totals["manual"]) / projected_active
            if projected_active
            else 1.0
        )
        before = covered_before / totals["active"] if totals["active"] else 1.0

        self.stdout.write("-" * 74)
        self.stdout.write(
            f"{'TOTAL':<22} {totals['auto']:>5} {totals['manual']:>7} {totals['dup']:>4} "
            f"{totals['unmatched']:>10} {totals['active']:>7} "
            f"{self._pct(before)}->{self._pct(projected):>6}"
        )
        self.stdout.write(
            f"buckets: matched auto={totals['auto']} / matched manual={totals['manual']} "
            f"/ unmatched={totals['unmatched']}"
        )
        self._print_details(reports)

        if fail_under is not None and projected * 100 < fail_under:
            raise CommandError(
                f"coverage {projected * 100:.1f}% below threshold {fail_under:.1f}% — "
                "see unmatched/duplicate detail above; do NOT flip BOOKING_VIA_AYLA_REST."
            )
        self.stdout.write(self.style.SUCCESS(f"projected coverage: {projected * 100:.1f}%"))

    def _load_mapping(self, path: str | None) -> dict:
        """Load + validate the JSON mapping file (C6 manual channel)."""
        if not path:
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"--mapping-file unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise CommandError(
                "--mapping-file must be a JSON object "
                '{"<ayla_id>": {"tenant_slug": ..., "service_slug": ...}}'
            )
        for key, target in data.items():
            if not isinstance(target, dict) or not (
                target.get("tenant_slug") and target.get("service_slug")
            ):
                raise CommandError(
                    f"--mapping-file entry {key!r} must carry tenant_slug + service_slug"
                )
        return data

    def _print_tenant(self, report: TenantLinkReport) -> None:
        before = report.covered_before / report.active_before if report.active_before else 1.0
        self.stdout.write(
            f"{report.tenant_slug:<22} {len(report.matched_auto):>5} "
            f"{len(report.matched_manual):>7} {len(report.duplicates):>4} "
            f"{len(report.unmatched):>10} {report.active_before:>7} "
            f"{self._pct(before)}->{self._pct(report.coverage_after):>6}"
        )

    def _print_details(self, reports: list[TenantLinkReport]) -> None:
        for report in reports:
            for key in report.manual_skipped:
                self.stdout.write(
                    self.style.WARNING(
                        f"MANUAL-SKIP {report.tenant_slug} mapping key {key} "
                        "not found upstream — dead entry, fix the mapping file"
                    )
                )
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
