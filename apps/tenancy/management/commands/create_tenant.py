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

### The primary key is NOT a local detail (DRF-1510)

``apps.catalog.services.sync`` fetches all three mirrors with
``?tenant=str(tenant.id)``: Ayla filters its catalog by the **Ayla Tenant
UUID**, and the bot's ``Tenant.id`` is expected to BE that UUID (see
``CatalogHttpClient.fetch_salon_services``). The slug never leaves this
database — it only names the row for ``sync_catalog --tenant <slug>``.

So a tenant minted with the model default (``uuid.uuid4``) mirrors
**nothing**: the three fetches return zero rows, the mirror stays empty,
the salon is invisible to clients, and the only signal is one
``catalog.sync.empty_fetch`` warning that reads identically to a salon
which genuinely sells nothing. ``--id`` exists so provisioning states the
upstream UUID up front instead of discovering the mismatch days later.

### ``--city`` is not decoration either

``apps.marketplace.discovery`` routes a city token in the client's query to
``tenant__city`` (``_bookable_qs(city=...)``, ``_known_cities()``). A tenant
whose ``city`` is blank is absent from every city-scoped answer and from the
set of cities the query parser is willing to recognise at all. There is no
backfill command anywhere in the repo; before DRF-1510 the only way to set
``city`` was the Django admin, by hand, per salon.

Usage::

    python manage.py create_tenant --slug formula-tela --name "Формула тела"
    python manage.py create_tenant --slug mednyy-kovsh --name "Медный ковш" \
        --id 0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0 --city "Пенза"
    python manage.py create_tenant --slug formula-tela --name "Формула тела" --dry-run

Exit codes:
  * 0 — success (created, or already exists)
  * 1 — slug / id validation failed, or an existing row contradicts the
    ``--id`` asked for (raised as ``CommandError``)
"""

from __future__ import annotations

import uuid

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
            "--id",
            dest="tenant_id",
            default=None,
            help=(
                "Ayla Tenant UUID to use as this row's primary key. Catalog "
                "sync fetches with ?tenant=<Tenant.id>, so a tenant created "
                "without this mirrors zero services and zero masters "
                "(DRF-1510). Omit ONLY for tenants with no Ayla catalog."
            ),
        )
        parser.add_argument(
            "--city",
            default=None,
            help=(
                "Salon city for marketplace discovery, e.g. «Пенза». Blank "
                "city = invisible to every city-scoped client query. On an "
                "existing tenant this backfills a blank city and never "
                "overwrites a non-blank one."
            ),
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

    def handle(
        self,
        *,
        slug: str,
        name: str,
        dry_run: bool,
        system: bool,
        tenant_id: str | None = None,
        city: str | None = None,
        **opts,
    ) -> None:
        # Parse ``--id`` before anything touches the DB. A malformed UUID must
        # fail as a CommandError, not as a psycopg DataError halfway through.
        pk: uuid.UUID | None = None
        if tenant_id:
            try:
                pk = uuid.UUID(str(tenant_id).strip())
            except (ValueError, AttributeError, TypeError) as exc:
                raise CommandError(
                    f"Invalid --id {tenant_id!r}: expected the Ayla Tenant UUID."
                ) from exc

        city_value = (city or "").strip()

        # Validate slug shape *before* hitting the DB. full_clean() runs the
        # custom ``Tenant.clean()`` which enforces the platform slug regex
        # (stricter than Django's stock SlugField — see apps/tenancy/models.py).
        #
        # Skip ``validate_unique`` here — uniqueness collisions are the
        # idempotent path, handled explicitly below via the ``all_objects``
        # lookup. Without this, re-running ``create_tenant`` with an existing
        # slug would surface as a CommandError instead of a no-op.
        candidate = Tenant(slug=slug, name=name, is_system=system, city=city_value)
        try:
            candidate.full_clean(exclude=["id"], validate_unique=False)
        except ValidationError as exc:
            # CommandError exits with code 1 — script-friendly failure mode.
            raise CommandError(f"Invalid tenant: {exc.message_dict}") from exc

        # A requested id already worn by a DIFFERENT slug is a provisioning
        # error, not an idempotent re-run: two salons cannot share one Ayla
        # tenant, and letting it through would make the second one's catalog
        # collide with the first one's. Fail loudly before any write.
        if pk is not None:
            clash = Tenant.all_objects.filter(id=pk).exclude(slug=slug).first()
            if clash is not None:
                raise CommandError(
                    f"--id {pk} is already used by tenant {clash.slug!r}. "
                    "Two tenants cannot share one Ayla tenant UUID."
                )

        # Check existence via ``all_objects`` so we don't accidentally create
        # a duplicate when a previously-deactivated tenant exists with the
        # same slug. Re-activation is an explicit admin action, not implicit
        # via this command.
        existing = Tenant.all_objects.filter(slug=slug).first()
        if existing is not None:
            self._report_existing(existing, pk=pk, city=city_value, dry_run=dry_run)
            return

        if dry_run:
            system_note = " [system]" if system else ""
            id_note = f", id={pk}" if pk is not None else ""
            city_note = f", city={city_value!r}" if city_value else ""
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] would create Tenant(slug={slug!r}, name={name!r}"
                    f"{id_note}{city_note}){system_note}"
                )
            )
            return

        create_kwargs: dict[str, object] = {
            "slug": slug,
            "name": name,
            "is_system": system,
            "city": city_value,
        }
        if pk is not None:
            create_kwargs["id"] = pk
        tenant = Tenant.objects.create(**create_kwargs)
        system_note = " [system]" if tenant.is_system else ""
        city_note = f", city={tenant.city!r}" if tenant.city else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Created tenant {tenant.slug!r} (id={tenant.id}, "
                f"name={tenant.name!r}{city_note}){system_note}"
            )
        )
        if pk is None:
            # Not an error — some tenants (global_bot, the KB corpus) have no
            # Ayla catalog at all. But for a salon this is the difference
            # between a mirror and an empty room, so it must be said out loud
            # at provisioning time rather than read off a sync counter later.
            self.stdout.write(
                self.style.WARNING(
                    "  no --id given: this row's primary key is a fresh uuid4, "
                    "so catalog sync will fetch 0 rows unless it happens to "
                    "match the Ayla Tenant UUID (DRF-1510)."
                )
            )

    # ------------------------------------------------------------------
    # Existing-row path
    # ------------------------------------------------------------------

    def _report_existing(
        self,
        existing: Tenant,
        *,
        pk: uuid.UUID | None,
        city: str,
        dry_run: bool,
    ) -> None:
        """Idempotent re-run: report, backfill a blank city, never clobber.

        Three distinct outcomes, all exit 0 except the id contradiction:

        * ``--id`` matching the stored pk — nothing to say beyond the no-op.
        * ``--id`` DIFFERING from the stored pk — the row cannot be re-keyed
          (its mirror rows hang off that pk), so the command says so instead
          of pretending the deploy is now correct. Loud, because this is the
          exact state that mirrors an empty catalog.
        * ``--city`` on a blank city — filled in. On a non-blank different
          city, reported and left alone: an operator's admin edit outranks a
          re-run of a provisioning script.
        """
        self.stdout.write(
            f"Tenant {existing.slug!r} already exists (id={existing.id}, "
            f"is_active={existing.is_active}). No-op."
        )
        if pk is not None and pk != existing.id:
            raise CommandError(
                f"Tenant {existing.slug!r} exists with id={existing.id}, but --id "
                f"asked for {pk}. The primary key cannot be changed in place — "
                "catalog sync will keep fetching with the stored id and mirror "
                "nothing. Fix the row (or delete and re-create it) explicitly."
            )
        if not city:
            return
        if existing.city and existing.city != city:
            self.stdout.write(
                self.style.WARNING(
                    f"  city already set to {existing.city!r}; --city {city!r} "
                    "ignored (this command never overwrites a non-blank city)."
                )
            )
            return
        if existing.city == city:
            return
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"  [dry-run] would backfill blank city -> {city!r}")
            )
            return
        existing.city = city
        existing.save(update_fields=["city", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"  backfilled blank city -> {city!r}"))
