"""Apply the §2 rule to every salon-assistant person (DRF-1700, S2-1).

    python manage.py resolve_salon_customers            # reads, prints the breakdown
    python manage.py resolve_salon_customers --apply    # writes — the owner's word

The schema migration set every shell to UNRESOLVED and guessed nothing.
This command is the guess made out loud: one line per person — MAX ID /
identity / phone / SHADOW — and the totals, so the owner can compare them
with the measurement (S2-0: real people 3 / 1 / 0 / 3, plus 7 fixtures)
before a single row is written. Names never appear; ids do, because ids are
what the owner is deciding about.

``--apply`` writes only UNRESOLVED shells (a re-run is a report, not a
second decision) and prints what it wrote.
"""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand

from apps.identity.services import salon_customer as sc


class Command(BaseCommand):
    help = (
        "Classify salon-assistant people as LINKED / SHADOW by the §2 rule. Reads unless --apply."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true", help="Write the classification.")

    def handle(self, *args, **options) -> None:
        apply = options["apply"]
        self.stdout.write(
            f"действие     {'ПРИМЕНЕНИЕ' if apply else 'сухой прогон, только чтение'}"
        )
        self.stdout.write(f"UNRESOLVED до: {sc.unresolved_count()}")
        self.stdout.write("")
        self.stdout.write(
            f"{'#':>3} {'channel:id':28} {'сопоставление':14} {'салонных':8} {'в global':8}"
        )

        totals: Counter[str] = Counter()
        written = 0
        for n, (channel, cid) in enumerate(sc.salon_people(), start=1):
            c = sc.classify(channel, cid)
            totals[c.match_by] += 1
            self.stdout.write(
                f"{n:>3} {channel + ':' + cid:28} {c.match_by:14} "
                f"{len(c.salon_shell_ids):8} {len(c.global_shell_ids):8}"
            )
            if apply:
                written += sc.apply_classification(c)

        self.stdout.write("")
        self.stdout.write(
            "итого        "
            + "  ".join(
                f"{k}={totals.get(k, 0)}"
                for k in (sc.MATCH_MAX_ID, sc.MATCH_IDENTITY, sc.MATCH_PHONE, sc.MATCH_SHADOW)
            )
            + f"  людей={sum(totals.values())}"
        )
        # People who only ever met the client bot: outside §2's list, LINKED /
        # client_bot by construction. Printed as their own line so the operator
        # sees them apart from the salon people the owner decided about.
        contour = sc.client_contour_only()
        self.stdout.write(f"клиентский контур без салонной оболочки: {len(contour)}")
        for channel, cid in contour:
            self.stdout.write(f"    {channel}:{cid:26} → LINKED / client_bot")
            if apply:
                written += sc.stamp_client_contour(channel, cid)
        if apply:
            self.stdout.write(f"записано строк: {written}")
        self.stdout.write(f"UNRESOLVED после: {sc.unresolved_count()}")


__all__ = ["Command"]
