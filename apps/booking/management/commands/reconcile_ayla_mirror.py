"""Manual run of the mirror ↔ canon reconciliation (DRF-1111/DRF-1161).

    python manage.py reconcile_ayla_mirror

The operator surface for the detector: after an incident, after a deploy
that touched the booking event path, or when the salon day looks wrong.
READ-ONLY — like the beat task it wraps, it writes to neither side, and
it never pages: the human running it IS the alert channel.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.booking.mirror_reconcile import run_mirror_reconciliation


class Command(BaseCommand):
    help = (
        "Compare live bookings in Ayla against the RemoteBookingProxy mirror, "
        "per tenant. Read-only; prints, never pages."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        summary = run_mirror_reconciliation(page=None)

        if not summary["configured"]:
            self.stdout.write("ayla seam not configured (AYLA_BASE_URL / token) — nothing swept")
            return

        reports = summary["reports"]
        self.stdout.write(f"checked clean:   {len(summary['checked'])}")
        for slug in summary["checked"]:
            self.stdout.write(f"  {slug}: clean")
        self.stdout.write(f"diverged:        {len(summary['diverged'])}")
        for slug in summary["diverged"]:
            report = reports[slug]
            self.stdout.write(
                f"  {slug}: ayla_only={len(report.ayla_only)}"
                f" mirror_only={len(report.mirror_only)}"
                f" status_mismatch={len(report.status_mismatch)}"
                f" start_mismatch={len(report.start_mismatch)}"
            )
            for kind, rows in (
                ("ayla_only", report.ayla_only),
                ("mirror_only", report.mirror_only),
                ("status_mismatch", report.status_mismatch),
                ("start_mismatch", report.start_mismatch),
            ):
                for row in rows:
                    self.stdout.write(f"    {kind}: {row}")
        for slug in summary["skipped_no_actor"]:
            self.stdout.write(f"  {slug}: SKIPPED — no active owner/admin to name to Ayla")
        for slug in summary["unchecked"]:
            self.stdout.write(f"  {slug}: UNCHECKED — Ayla read failed (see logs)")
