"""Read-only identity card for one messenger account (owner 11.09 §12).

    python manage.py identity_card --account max:83146139

Prints what the operator may see: tenants by slug, roles, master card,
dates, counts — and every personal value masked (name: first letter and
length; phone: presence and length, never a digit — DRF-1039). Reads only.
The status line is §12.4: BLOCKED_BY_IDENTITY until a person is confirmed;
freeing is only ever `reset_test_account` on both sides.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.identity.services.identity_card import build_card, render_for_operator


class Command(BaseCommand):
    help = "Print a masked, read-only identity card for channel:channel_user_id."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--account", action="append", required=True, metavar="CHANNEL:ID", help="max:83146139"
        )

    def handle(self, *args, **options) -> None:
        for spec in options["account"]:
            channel, sep, channel_user_id = spec.partition(":")
            if not sep or not channel or not channel_user_id:
                raise CommandError(f"account must look like channel:channel_user_id, got {spec!r}")
            self.stdout.write(render_for_operator(build_card(channel, channel_user_id)))
            self.stdout.write("")


__all__ = ["Command"]
