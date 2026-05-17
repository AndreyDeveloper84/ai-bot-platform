"""Idempotent tenant creation command (DRF-419 / Sprint 1 / A2).

Adapted from Ayla ``origin/dev:tenants/management/commands/backfill_tenants.py``
with the FK-backfill steps stripped — the platform has no other models with
a ``tenant`` FK in Sprint 1, so the command's job reduces to:

    1. Validate the slug shape via ``Tenant.full_clean()`` *before* save.
    2. ``get_or_create(slug=...)`` to stay idempotent under re-runs.
    3. ``--dry-run`` reports intent without writing.

Why a management command instead of "just use the admin":
  * Operations / CI can script tenant provisioning without Django
    admin auth.
  * Idempotent re-runs match infra-as-code patterns (Ansible,
    docker-compose ``up`` boot steps).
  * Initial tenant provisioning happens before any user exists — admin
    isn't reachable yet.

Usage::

    python manage.py create_tenant --slug formula-tela --name "Формула тела"
    python manage.py create_tenant --slug formula-tela --name "Формула тела" --dry-run

Exit codes:
  * 0 — success (created or already exists)
  * 1 — slug validation failed (raised as ``CommandError``)
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Create a tenant (idempotent). Validates slug shape before save."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--slug",
            required=True,
            help=(
                "Tenant slug — lowercase alphanumeric with hyphen/underscore, "
                "2-50 chars, must start with a letter or digit."
            ),
        )
        parser.add_argument(
            "--name",
            required=True,
            help="Human-readable tenant name shown in admin and billing UI.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print intent without writing to the database.",
        )
        parser.add_argument(
            "--system",
            action="store_true",
            help=(
                "Mark the tenant as a service tenant (e.g. shared KB corpus). "
                "Service tenants cannot be deleted from admin. KB-RAG Sub-1 / GH #114."
            ),
        )

    def handle(self, *, slug: str, name: str, dry_run: bool, system: bool, **opts) -> None:
        # Validate slug shape *before* hitting the DB. full_clean() runs the
        # custom ``Tenant.clean()`` which enforces the platform slug regex
        # (stricter than Django's stock SlugField — see apps/tenancy/models.py).
        #
        # Skip ``validate_unique`` here — uniqueness collisions are the
        # idempotent path, handled explicitly below via the ``all_objects``
        # lookup. Without this, re-running ``create_tenant`` with an existing
        # slug would surface as a CommandError instead of a no-op.
        candidate = Tenant(slug=slug, name=name, is_system=system)
        try:
            candidate.full_clean(exclude=["id"], validate_unique=False)
        except ValidationError as exc:
            # CommandError exits with code 1 — script-friendly failure mode.
            raise CommandError(f"Invalid tenant: {exc.message_dict}") from exc

        # Check existence via ``all_objects`` so we don't accidentally create
        # a duplicate when a previously-deactivated tenant exists with the
        # same slug. Re-activation is an explicit admin action, not implicit
        # via this command.
        existing = Tenant.all_objects.filter(slug=slug).first()
        if existing is not None:
            self.stdout.write(
                f"Tenant {slug!r} already exists (id={existing.id}, "
                f"is_active={existing.is_active}). No-op."
            )
            return

        if dry_run:
            system_note = " [system]" if system else ""
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] would create Tenant(slug={slug!r}, name={name!r}){system_note}"
                )
            )
            return

        tenant = Tenant.objects.create(slug=slug, name=name, is_system=system)
        system_note = " [system]" if tenant.is_system else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Created tenant {tenant.slug!r} (id={tenant.id}, "
                f"name={tenant.name!r}){system_note}"
            )
        )
