"""Report ``CatalogService.ayla_service_id`` back-fill coverage per tenant.

Read-only diagnostic feeding the 🔴 pre-flip question for #1016 / brief
#1034: *is ``ayla_service_id`` populated for the majority of live
services?* Under ``BOOKING_VIA_AYLA_REST`` the booking health-check gate
grounds on this column and FAILS CLOSED on a miss (routes to a human), so
low coverage is SAFE but degrades UX — every un-grounded service dead-ends
into manual handoff. This command quantifies that exposure before the flip.

Coverage = active ``CatalogService`` rows with a non-null ``ayla_service_id``
÷ all active rows, per tenant.

Usage::

    python manage.py ayla_service_id_coverage              # all tenants
    python manage.py ayla_service_id_coverage --tenant-slug formula-tela
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.catalog.models import CatalogService
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Report CatalogService.ayla_service_id back-fill coverage per tenant."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--tenant-slug",
            default=None,
            help="Limit the report to a single tenant (default: all tenants).",
        )

    def handle(self, *args, **options) -> None:
        tenant_slug = options["tenant_slug"]
        tenants = Tenant.objects.all().order_by("slug")
        if tenant_slug:
            tenants = tenants.filter(slug=tenant_slug)

        total_active = 0
        total_grounded = 0
        self.stdout.write(f"{'tenant':<28} {'grounded':>9} {'active':>7} {'coverage':>9}")
        self.stdout.write("-" * 56)

        for tenant in tenants:
            active_qs = CatalogService.all_tenants.filter(tenant=tenant, is_active=True)
            active = active_qs.count()
            grounded = active_qs.filter(ayla_service_id__isnull=False).count()
            total_active += active
            total_grounded += grounded
            pct = f"{(grounded / active * 100):.1f}%" if active else "n/a"
            self.stdout.write(f"{tenant.slug:<28} {grounded:>9} {active:>7} {pct:>9}")

        self.stdout.write("-" * 56)
        overall = f"{(total_grounded / total_active * 100):.1f}%" if total_active else "n/a"
        self.stdout.write(f"{'TOTAL':<28} {total_grounded:>9} {total_active:>7} {overall:>9}")

        if total_active and total_grounded / total_active < 0.5:
            self.stdout.write(
                self.style.WARNING(
                    "\nCoverage < 50%: SAFE (health-check fails closed) but flag-ON would "
                    "route the majority of confirms to manual handoff. Do NOT flip "
                    "BOOKING_VIA_AYLA_REST until coverage is high — escalate to founder."
                )
            )
        elif total_active:
            self.stdout.write(self.style.SUCCESS("\nCoverage ≥ 50%."))
