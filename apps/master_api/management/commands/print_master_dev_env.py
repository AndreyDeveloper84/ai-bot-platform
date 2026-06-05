"""Print a .env.local snippet for the DEBUG-only init-data bypass.

Usage::

    python manage.py print_master_dev_env <master_id>

Looks up the :class:`apps.catalog.models.CatalogMaster` by id and prints
its linked BotUser id + tenant slug as a ready-to-paste snippet for
``apps/miniapp/.env.local``. The frontend (``apps/miniapp/src/lib/dev-bypass.ts``)
then injects ``X-Dev-Bypass``, ``X-Dev-User-Id`` and ``X-Dev-Tenant-Slug``
headers on every API call so the master Mini App works in a plain Chrome
tab — see ``docs/runbooks/master-bot-onboarding.md`` for the full flow.

Errors out when the master has no linked BotUser yet (i.e. M0 onboarding
not complete). In that bootstrap case, use::

    python manage.py create_test_master_invite ... --bootstrap-bot-user

which pre-creates a placeholder BotUser and prints the same snippet
immediately, so the dev can complete M0 in the browser.

This command does NOT enable the bypass — that's purely the DEBUG setting
on the backend. It only assists the dev in wiring up the correct env vars.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import CatalogMaster


class Command(BaseCommand):
    help = (
        "Print a .env.local snippet (VITE_DEV_BYPASS_USER_ID + "
        "VITE_DEV_BYPASS_TENANT_SLUG) for the given master. Requires "
        "the master to be linked to a BotUser (M0 onboarding complete)."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "master_id",
            help="CatalogMaster.id (UUID) of the master whose linked "
            "BotUser should be used for the dev-bypass snippet.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        raw_id: str = options["master_id"]
        try:
            master_uuid = uuid.UUID(raw_id)
        except (TypeError, ValueError) as exc:
            raise CommandError(f"master_id {raw_id!r} is not a valid UUID") from exc

        try:
            master = CatalogMaster.all_tenants.select_related("tenant", "linked_bot_user").get(
                id=master_uuid
            )
        except CatalogMaster.DoesNotExist as exc:
            raise CommandError(f"CatalogMaster {raw_id} not found") from exc

        if master.linked_bot_user is None:
            raise CommandError(
                f"CatalogMaster {raw_id} has no linked_bot_user — complete M0 "
                "onboarding first, OR re-run create_test_master_invite with "
                "--bootstrap-bot-user to pre-create a placeholder BotUser."
            )

        bot_user = master.linked_bot_user
        tenant = master.tenant

        self.stdout.write("# paste into apps/miniapp/.env.local:")
        self.stdout.write(f"VITE_DEV_BYPASS_USER_ID={bot_user.id}")
        self.stdout.write(f"VITE_DEV_BYPASS_TENANT_SLUG={tenant.slug}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "# Remember: this works ONLY when the Django backend runs "
                "with DEBUG=True. Never works in prod."
            )
        )
