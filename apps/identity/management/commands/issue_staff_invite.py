"""Issue a staff invite code from the command line (DRF-1061).

### Why a command exists at all

Chicken and egg: the admin screen that issues invites is itself behind
``@require_admin_role``, and on a fresh salon there are no admins. Someone
has to make the first one from outside the loop. After that the salon is
self-sufficient — an owner invites the rest from the Mini App.

### Usage

    python manage.py issue_staff_invite --tenant formula-tela --role owner
    python manage.py issue_staff_invite --tenant formula-tela --role master \\
        --master-name "Тихонова Ольга"
    python manage.py issue_staff_invite --tenant formula-tela --role master \\
        --list-masters

The code is printed **once** and is not recoverable — only its hash is
stored. Hand it to the person over any channel they can read; it is
single-use and expires in 7 days, so a leaked code is a small blast radius
and a re-issue is cheap.

### Master invites link, never create

``--master-name`` picks an EXISTING catalog row. There is deliberately no
flag to create a master here: all four pilot masters are already in the
mirror, and a duplicate would be invisible to the booking mirror — whose
``specialist_id`` points at the original — leaving that master looking at
an empty day next to their real appointments.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import CatalogMaster
from apps.identity.services.staff_invites import INVITE_TTL_DAYS, issue_staff_invite
from apps.tenancy.models import StaffInvite, Tenant


class Command(BaseCommand):
    help = "Issue a one-shot staff invite code for a salon. Prints the code once."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="Tenant slug, e.g. formula-tela.")
        parser.add_argument(
            "--role",
            required=True,
            choices=[c[0] for c in StaffInvite.Role.choices],
            help="owner | admin | receptionist | master.",
        )
        parser.add_argument(
            "--master-name",
            default="",
            help="For --role=master: name of the EXISTING catalog master to link.",
        )
        parser.add_argument(
            "--master-id",
            default="",
            help="For --role=master: id of the existing catalog master "
            "(use when two masters share a name).",
        )
        parser.add_argument(
            "--list-masters",
            action="store_true",
            help="List the tenant's masters and their link state, then exit.",
        )
        parser.add_argument(
            "--note",
            default="",
            help="Label for your own records — who this code is for.",
        )
        parser.add_argument(
            "--ttl-days",
            type=int,
            default=INVITE_TTL_DAYS,
            help=f"Validity in days (default {INVITE_TTL_DAYS}).",
        )

    def handle(self, *args, **options) -> None:
        tenant = self._get_tenant(options["tenant"])

        if options["list_masters"]:
            self._list_masters(tenant)
            return

        role = options["role"]
        catalog_master = None
        if role == StaffInvite.Role.MASTER:
            catalog_master = self._get_master(tenant, options)
        elif options.get("master_name") or options.get("master_id"):
            # Refuse rather than ignore. `--role admin --master-name "Ольга"`
            # is almost certainly a mistyped role, and silently issuing an
            # ADMIN code while the operator believes they invited a master
            # hands out more access than intended.
            raise CommandError(
                f"--master-name/--master-id are only valid with --role=master, "
                f"got --role={role}. Did you mean --role=master?"
            )

        invite, code = issue_staff_invite(
            tenant=tenant,
            role=role,
            catalog_master=catalog_master,
            note=options["note"],
            ttl_days=options["ttl_days"],
        )

        target = f" → {catalog_master.name}" if catalog_master else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Invite issued for {tenant.slug} / {role}{target}\n"
                f"  code:    {code}\n"
                f"  expires: {invite.expires_at:%Y-%m-%d %H:%M} UTC\n"
                f"  id:      {invite.id}"
            )
        )
        # Said plainly because the operator only gets one chance to copy it.
        self.stdout.write(
            "\nThe code is shown once — only its hash is stored. "
            "Re-issue if it is lost.\n"
            "The recipient can either type it into the salon bot or open:\n"
            f"  max://bot/<salon_bot>?start=inv_{code.replace('-', '')}"
        )

    def _get_tenant(self, slug: str) -> Tenant:
        tenant = Tenant.all_objects.filter(slug=slug).first()
        if tenant is None:
            known = ", ".join(Tenant.all_objects.values_list("slug", flat=True)[:20]) or "none"
            raise CommandError(f"No tenant with slug {slug!r}. Known: {known}")
        return tenant

    def _list_masters(self, tenant: Tenant) -> None:
        masters = CatalogMaster.all_tenants.filter(tenant=tenant).order_by("name")
        if not masters:
            self.stdout.write(f"No masters in {tenant.slug}.")
            return
        self.stdout.write(f"Masters in {tenant.slug}:")
        for master in masters:
            linked = "linked" if master.linked_bot_user_id else "NOT linked"
            archived = " [archived]" if master.archived_at else ""
            active = "" if master.is_active else " [inactive]"
            self.stdout.write(f"  {master.id}  {master.name}  — {linked}{archived}{active}")

    def _get_master(self, tenant: Tenant, options: dict) -> CatalogMaster:
        master_id = (options.get("master_id") or "").strip()
        master_name = (options.get("master_name") or "").strip()

        if not master_id and not master_name:
            raise CommandError(
                "--role=master needs --master-name or --master-id. "
                "Run with --list-masters to see them."
            )

        qs = CatalogMaster.all_tenants.filter(tenant=tenant, archived_at__isnull=True)
        if master_id:
            try:
                master = qs.filter(pk=master_id).first()
            except (ValidationError, ValueError) as exc:
                # A malformed id is an operator typo, not a crash.
                raise CommandError(f"{master_id!r} is not a valid master id.") from exc
            if master is None:
                raise CommandError(f"No active master {master_id!r} in {tenant.slug}.")
            self._warn_if_linked(master)
            return master

        matches = list(qs.filter(name__iexact=master_name))
        if not matches:
            # Fall back to a contains match so a partial name is usable.
            matches = list(qs.filter(name__icontains=master_name))
        if not matches:
            raise CommandError(
                f"No active master matching {master_name!r} in {tenant.slug}. "
                "Run with --list-masters to see them."
            )
        if len(matches) > 1:
            listed = "\n".join(f"    {m.id}  {m.name}" for m in matches)
            raise CommandError(
                f"{len(matches)} masters match {master_name!r} — pass --master-id:\n{listed}"
            )

        master = matches[0]
        self._warn_if_linked(master)
        return master

    def _warn_if_linked(self, master: CatalogMaster) -> None:
        """Warn before handing out a code that would move an existing link.

        Applies to BOTH lookup paths. It used to fire only for --master-name,
        which is backwards: --master-id is what the command recommends for
        the ambiguous case, so the operator most at risk of re-pointing the
        wrong person's link was the one not being warned.
        """

        if not master.linked_bot_user_id:
            return
        # Not fatal — re-inviting is legitimate when someone changes their
        # MAX account — but it should be a conscious act.
        self.stdout.write(
            self.style.WARNING(
                f"NOTE: {master.name} is already linked to a bot user. "
                "Redeeming this code will move the link to whoever uses it."
            )
        )
