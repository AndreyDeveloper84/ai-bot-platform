"""Create or refresh a PENDING master invite for manual M0 testing.

Usage::

    python manage.py create_test_master_invite \\
        --tenant formula-tela \\
        --name "Анна Петрова" \\
        --max-handle anna_styl

The command prints the master's id, invite_token, expires_at, the MAX
bot deeplink (``max://bot/<bot>?start=master_invite_<token>``) and the
Mini App web URL (``<host>/onboarding/master?token=<token>``).

The host is read from ``settings.SITE_DOMAIN``; if absent, falls back to
``http://localhost:5173`` (the Vite dev default). Override the bot slug
with ``--bot``; defaults to ``settings.MASTER_BOT_USERNAME`` or
``<tenant-slug>_bot``.

Idempotency: if a master with the same ``--name`` already has a PENDING
invite in this tenant, the existing row is reused (no new token), so
re-running the command produces the same deeplink. Pass ``--regenerate``
to force a fresh token + extend expiry by 7 days.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.master_api.auth import generate_invite_token
from apps.tenancy.models import Tenant


DEFAULT_DEV_HOST = "http://localhost:5173"
INVITE_TTL_DAYS = 7


class Command(BaseCommand):
    help = (
        "Create or refresh a PENDING CatalogMaster row + invite token "
        "for manual M0 onboarding testing. Idempotent on (tenant, name)."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--tenant",
            required=True,
            help="Tenant slug to attach the master to (e.g. 'formula-tela').",
        )
        parser.add_argument(
            "--name",
            required=True,
            help="Master full name. Used as the idempotency key with --tenant.",
        )
        parser.add_argument(
            "--max-handle",
            default="",
            help="Master's MAX handle (without @). Optional but renders in Step 1 identity card.",
        )
        parser.add_argument(
            "--specialization",
            default="",
            help="Master specialization label (optional).",
        )
        parser.add_argument(
            "--bot",
            default=None,
            help="Bot slug for the deeplink. Defaults to "
            "settings.MASTER_BOT_USERNAME or '<tenant_slug>_bot'.",
        )
        parser.add_argument(
            "--regenerate",
            action="store_true",
            help="If a PENDING master with this (tenant, name) already exists, "
            "rotate the invite_token and extend expiry by 7 days.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        # PR 3 / MM2 deprecation: this command fabricates the invite
        # row directly, bypassing the audit + dispatch side effects of
        # the real flow. Prefer ``POST /api/v1/admin/masters/invite``
        # via the admin Mini App / dashboard. The command is kept for
        # backend tests that don't want HTTP overhead and for the very
        # earliest "no admin exists yet" bootstrap of a fresh tenant.
        self.stderr.write(
            self.style.WARNING(
                "[DEPRECATED] create_test_master_invite bypasses the proper "
                "invite flow (no audit row, no MAX DM dispatch, no idempotency). "
                "For real testing, use POST /api/v1/admin/masters/invite via the "
                "admin Mini App or dashboard. This command will be removed in a "
                "later PR — see docs/runbooks/master-bot-onboarding.md."
            )
        )

        tenant_slug: str = options["tenant"]
        name: str = options["name"].strip()
        if not name:
            raise CommandError("--name must not be blank")

        max_handle: str = options["max_handle"].lstrip("@")
        specialization: str = options["specialization"]
        regenerate: bool = options["regenerate"]

        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"tenant {tenant_slug!r} not found") from exc

        now = timezone.now()
        expires_at = now + timedelta(days=INVITE_TTL_DAYS)

        existing = (
            CatalogMaster.all_tenants.filter(
                tenant=tenant,
                name=name,
                invite_status=CatalogMaster.InviteStatus.PENDING,
            )
            .order_by("-invited_at")
            .first()
        )

        created = False
        if existing is None:
            ext_id = CatalogMaster.all_tenants.filter(tenant=tenant).count() + 10_000
            master = CatalogMaster.all_tenants.create(
                tenant=tenant,
                external_id=ext_id,
                external_updated_at=now,
                name=name,
                specialization=specialization,
                is_active=True,
                invite_status=CatalogMaster.InviteStatus.PENDING,
                invite_token=generate_invite_token(),
                invite_expires_at=expires_at,
                invited_at=now,
                max_handle=max_handle,
                mode=CatalogMaster.Mode.INVITE,
            )
            created = True
        else:
            master = existing
            update_fields: list[str] = []
            if regenerate:
                master.invite_token = generate_invite_token()
                master.invite_expires_at = expires_at
                master.invited_at = now
                update_fields += ["invite_token", "invite_expires_at", "invited_at"]
            if max_handle and master.max_handle != max_handle:
                master.max_handle = max_handle
                update_fields.append("max_handle")
            if specialization and master.specialization != specialization:
                master.specialization = specialization
                update_fields.append("specialization")
            if update_fields:
                master.save(update_fields=update_fields)

        bot_slug = (
            options["bot"] or getattr(settings, "MASTER_BOT_USERNAME", "") or f"{tenant.slug}_bot"
        )
        host = getattr(settings, "SITE_DOMAIN", "") or DEFAULT_DEV_HOST
        if "://" not in host:
            host = f"https://{host}"

        token = master.invite_token
        # Both fields are populated by either the create-new branch above
        # OR the regenerate branch; reuse keeps the prior non-NULL values.
        assert token is not None  # noqa: S101 — invariant guard for typing
        assert master.invite_expires_at is not None  # noqa: S101
        deeplink = f"max://bot/{bot_slug}?start=master_invite_{token}"
        web_url = f"{host.rstrip('/')}/onboarding/master?token={token}"

        verb = "Created" if created else ("Refreshed" if regenerate else "Reusing")
        self.stdout.write(self.style.SUCCESS(f"\N{CHECK MARK} {verb} test master invite"))
        self.stdout.write(f"  master_id:     {master.id}")
        self.stdout.write(f"  tenant:        {tenant.slug}")
        self.stdout.write(f"  invite_token:  {token}")
        self.stdout.write(f"  expires_at:    {master.invite_expires_at.isoformat()}")
        self.stdout.write(f"  deeplink:      {deeplink}")
        self.stdout.write(f"  web URL:       {web_url}")
