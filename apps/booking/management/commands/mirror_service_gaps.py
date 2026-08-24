"""How many mirrored bookings the day board cannot name (DRF-1103).

READ-ONLY. This command counts and prints; it writes nothing, and it must
stay that way. Backfilling history on a live pilot is a separate decision
with a separate owner — the question this answers is «how big is it», which
has to be answerable BEFORE anyone decides whether to touch anything.

Run it before and after a deploy of DRF-1110:

    python manage.py mirror_service_gaps

The number that matters is the LIVE one. A cancelled booking with no service
on it is a row nobody will ever open again; a confirmed one is a customer
walking through the door on Tuesday and a front desk that cannot tell what
they are here for.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.booking.mirror_status import LIVE_STATUSES
from apps.booking.models import RemoteBookingProxy


class Command(BaseCommand):
    help = "Count mirror rows with no service_id, split by source and liveness. Read-only."

    def handle(self, *args: Any, **options: Any) -> None:
        rows = RemoteBookingProxy.all_tenants.all()
        total = rows.count()
        missing = rows.filter(service_id__isnull=True)
        missing_total = missing.count()

        # ``status`` is a mirror of Ayla's wire value and is NOT constrained to
        # the model's choices (``awaiting_payment`` is on the pilot right now),
        # so liveness is tested against the shared vocabulary rather than
        # against Status — see apps/booking/mirror_status.py.
        live_missing = missing.filter(status__in=LIVE_STATUSES).count()

        self.stdout.write(f"mirror rows total:              {total}")
        self.stdout.write(f"  of which service_id IS NULL:  {missing_total}")
        self.stdout.write(f"    still live (day board):     {live_missing}")
        self.stdout.write("")

        self.stdout.write("by source (service_id IS NULL):")
        # ``source`` is blank on rows created by an update event that does not
        # repeat it, so the empty string is a real bucket and is printed as
        # such rather than folded into any named source.
        for source in sorted(
            {str(value or "") for value in missing.values_list("source", flat=True)}
        ):
            count = missing.filter(source=source).count()
            self.stdout.write(f"  {source or '(blank)':<16} {count}")

        self.stdout.write("")
        self.stdout.write("by status (service_id IS NULL):")
        for status in sorted(
            {str(value or "") for value in missing.values_list("status", flat=True)}
        ):
            count = missing.filter(status=status).count()
            live = "live" if status in LIVE_STATUSES else "terminal"
            self.stdout.write(f"  {status or '(blank)':<20} {count:>5}  {live}")
