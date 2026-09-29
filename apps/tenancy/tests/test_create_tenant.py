"""``manage.py create_tenant`` command tests (DRF-419 / Sprint 1 / A2).

Covers the four behaviours that Operations / CI rely on:

    1. Happy path — argparse parses --slug + --name, tenant is created.
    2. Idempotency — re-running with same slug is a no-op, not an error.
    3. ``--dry-run`` — prints intent, doesn't write to the database.
    4. Slug validation — invalid shapes raise ``CommandError`` (exit code 1)
       *before* hitting the database.
"""

from __future__ import annotations

import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class TestCreateTenantHappyPath:
    def test_creates_active_tenant(self):
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "formula-tela",
            "--name",
            "Формула тела",
            stdout=out,
        )

        # DB state: tenant exists, is_active=True.
        tenant = Tenant.all_objects.get(slug="formula-tela")
        assert tenant.name == "Формула тела"
        assert tenant.is_active is True

        # User-facing output: includes "Created" success line.
        rendered = out.getvalue()
        assert "Created tenant" in rendered
        assert "formula-tela" in rendered


class TestCreateTenantIdempotency:
    def test_rerun_is_noop(self):
        # First run creates.
        call_command(
            "create_tenant",
            "--slug",
            "idemp",
            "--name",
            "Idempotent",
            stdout=StringIO(),
        )
        assert Tenant.all_objects.filter(slug="idemp").count() == 1

        # Second run with same slug: must not duplicate, must not raise.
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "idemp",
            "--name",
            "Idempotent (renamed input ignored)",
            stdout=out,
        )

        # Still exactly one row, original name untouched (no implicit rename).
        rows = Tenant.all_objects.filter(slug="idemp")
        assert rows.count() == 1
        assert rows.first().name == "Idempotent"

        # Output reports the no-op explicitly.
        assert "already exists" in out.getvalue()

    def test_rerun_with_inactive_tenant_is_noop_not_resurrection(self):
        # Operator deactivates a tenant; running create_tenant again
        # should NOT silently resurrect it. Re-activation is explicit.
        Tenant.objects.create(slug="zombie", name="Zombie", is_active=False)

        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "zombie",
            "--name",
            "Zombie",
            stdout=out,
        )

        zombie = Tenant.all_objects.get(slug="zombie")
        assert zombie.is_active is False  # NOT resurrected
        assert "already exists" in out.getvalue()


class TestCreateTenantDryRun:
    def test_dry_run_prints_intent_without_writing(self):
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "would-be",
            "--name",
            "Would Be",
            "--dry-run",
            stdout=out,
        )

        # Nothing in the database.
        assert not Tenant.all_objects.filter(slug="would-be").exists()

        # User-facing output indicates dry-run.
        rendered = out.getvalue()
        assert "[dry-run]" in rendered
        assert "would create" in rendered
        assert "would-be" in rendered


class TestCreateTenantSystemFlag:
    def test_system_flag_marks_tenant_as_system(self):
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "global-kb",
            "--name",
            "Global KB",
            "--system",
            stdout=out,
        )

        tenant = Tenant.all_objects.get(slug="global-kb")
        assert tenant.is_system is True
        assert tenant.is_active is True  # system tenants are still active

    def test_without_flag_tenant_is_regular(self):
        call_command(
            "create_tenant",
            "--slug",
            "salon-regular",
            "--name",
            "Regular Salon",
            stdout=StringIO(),
        )

        tenant = Tenant.all_objects.get(slug="salon-regular")
        assert tenant.is_system is False

    def test_dry_run_with_system_flag_writes_nothing(self):
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "ghost-kb",
            "--name",
            "Ghost",
            "--system",
            "--dry-run",
            stdout=out,
        )
        assert not Tenant.all_objects.filter(slug="ghost-kb").exists()


class TestCreateTenantSlugValidation:
    def test_uppercase_slug_rejected(self):
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "create_tenant",
                "--slug",
                "UPPERCASE",
                "--name",
                "X",
                stdout=StringIO(),
            )
        # Validation must reference slug field.
        assert "slug" in str(exc_info.value).lower()
        # Critically: nothing was written.
        assert not Tenant.all_objects.filter(slug="UPPERCASE").exists()

    def test_slug_with_leading_hyphen_rejected(self):
        with pytest.raises(CommandError):
            call_command(
                "create_tenant",
                "--slug",
                "-bad-start",
                "--name",
                "X",
                stdout=StringIO(),
            )
        assert not Tenant.all_objects.filter(slug="-bad-start").exists()

    def test_too_short_slug_rejected(self):
        with pytest.raises(CommandError):
            call_command(
                "create_tenant",
                "--slug",
                "a",
                "--name",
                "X",
                stdout=StringIO(),
            )
        assert not Tenant.all_objects.filter(slug="a").exists()


class TestCreateTenantAylaId:
    """``--id`` — the pk IS the Ayla Tenant UUID (DRF-1510).

    ``apps.catalog.services.sync`` fetches every mirror with
    ``?tenant=str(tenant.id)``. A tenant minted with the model's ``uuid4``
    default therefore mirrors zero rows while every counter reads healthy,
    which is how five salons stayed invisible with 171 services behind them.
    """

    def test_id_becomes_the_primary_key(self):
        wanted = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")
        call_command(
            "create_tenant",
            "--slug",
            "olhovyy-dvor",
            "--name",
            "Ольховый двор",
            "--id",
            str(wanted),
            stdout=StringIO(),
        )
        assert Tenant.all_objects.get(slug="olhovyy-dvor").id == wanted

    def test_without_id_the_pk_is_a_fresh_uuid4_and_the_command_says_so(self):
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "no-upstream",
            "--name",
            "No Upstream",
            stdout=out,
        )
        assert Tenant.all_objects.filter(slug="no-upstream").exists()
        # Not an error — global_bot and the KB corpus legitimately have no
        # Ayla catalog — but it must never be silent for a salon.
        assert "no --id given" in out.getvalue()
        assert "mirror 0 services" in out.getvalue()

    def test_malformed_id_is_rejected_before_any_write(self):
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "create_tenant",
                "--slug",
                "bad-id",
                "--name",
                "Bad",
                "--id",
                "not-a-uuid",
                stdout=StringIO(),
            )
        assert "--id" in str(exc_info.value)
        assert not Tenant.all_objects.filter(slug="bad-id").exists()

    def test_id_already_worn_by_another_slug_is_refused(self):
        shared = uuid.uuid4()
        call_command(
            "create_tenant",
            "--slug",
            "first-salon",
            "--name",
            "First",
            "--id",
            str(shared),
            stdout=StringIO(),
        )
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "create_tenant",
                "--slug",
                "second-salon",
                "--name",
                "Second",
                "--id",
                str(shared),
                stdout=StringIO(),
            )
        assert "already used by tenant" in str(exc_info.value)
        assert not Tenant.all_objects.filter(slug="second-salon").exists()

    def test_rerun_with_the_same_id_stays_a_no_op(self):
        wanted = uuid.uuid4()
        for _ in range(2):
            call_command(
                "create_tenant",
                "--slug",
                "stable",
                "--name",
                "Stable",
                "--id",
                str(wanted),
                stdout=StringIO(),
            )
        rows = Tenant.all_objects.filter(slug="stable")
        assert rows.count() == 1
        assert rows.first().id == wanted

    def test_rerun_with_a_different_id_raises_instead_of_reporting_success(self):
        """A pk cannot be re-keyed in place; pretending otherwise is worse
        than failing, because the operator's evidence would be a lie."""
        call_command(
            "create_tenant",
            "--slug",
            "wrong-pk",
            "--name",
            "Wrong",
            stdout=StringIO(),
        )
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "create_tenant",
                "--slug",
                "wrong-pk",
                "--name",
                "Wrong",
                "--id",
                str(uuid.uuid4()),
                stdout=StringIO(),
            )
        assert "cannot be changed in place" in str(exc_info.value)

    def test_dry_run_with_id_writes_nothing(self):
        out = StringIO()
        wanted = uuid.uuid4()
        call_command(
            "create_tenant",
            "--slug",
            "ghost-id",
            "--name",
            "Ghost",
            "--id",
            str(wanted),
            "--dry-run",
            stdout=out,
        )
        assert not Tenant.all_objects.filter(slug="ghost-id").exists()
        assert str(wanted) in out.getvalue()


class TestCreateTenantCity:
    """``--city`` — ``Tenant.city`` drives city-scoped marketplace discovery.

    ``apps.marketplace.discovery._bookable_qs(city=...)`` filters on
    ``tenant__city``, and ``_known_cities()`` only recognises a city token
    that some bookable tenant actually carries. Blank city = absent from
    every city-scoped answer. There is no backfill command in the repo, so
    before this flag the only setter was the admin.
    """

    def test_city_is_stored(self):
        call_command(
            "create_tenant",
            "--slug",
            "mednyy-kovsh",
            "--name",
            "Медный ковш",
            "--city",
            "Пенза",
            stdout=StringIO(),
        )
        assert Tenant.all_objects.get(slug="mednyy-kovsh").city == "Пенза"

    def test_city_is_stripped(self):
        call_command(
            "create_tenant",
            "--slug",
            "spacey",
            "--name",
            "Spacey",
            "--city",
            "  Пенза  ",
            stdout=StringIO(),
        )
        assert Tenant.all_objects.get(slug="spacey").city == "Пенза"

    def test_without_city_the_field_stays_blank_not_null(self):
        call_command(
            "create_tenant",
            "--slug",
            "citiless",
            "--name",
            "Cityless",
            stdout=StringIO(),
        )
        assert Tenant.all_objects.get(slug="citiless").city == ""

    def test_rerun_backfills_a_blank_city(self):
        """The five already-connected salons predate ``--city``; a re-run of
        the provisioning line is how their blank city gets filled."""
        call_command(
            "create_tenant",
            "--slug",
            "legacy-salon",
            "--name",
            "Legacy",
            stdout=StringIO(),
        )
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "legacy-salon",
            "--name",
            "Legacy",
            "--city",
            "Пенза",
            stdout=out,
        )
        assert Tenant.all_objects.get(slug="legacy-salon").city == "Пенза"
        assert "backfilled blank city" in out.getvalue()

    def test_rerun_never_overwrites_a_non_blank_city(self):
        """An operator's admin edit outranks a re-run of a deploy script."""
        call_command(
            "create_tenant",
            "--slug",
            "moved",
            "--name",
            "Moved",
            "--city",
            "Москва",
            stdout=StringIO(),
        )
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "moved",
            "--name",
            "Moved",
            "--city",
            "Пенза",
            stdout=out,
        )
        assert Tenant.all_objects.get(slug="moved").city == "Москва"
        assert "ignored" in out.getvalue()

    def test_dry_run_backfill_writes_nothing(self):
        call_command(
            "create_tenant",
            "--slug",
            "dry-backfill",
            "--name",
            "Dry",
            stdout=StringIO(),
        )
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "dry-backfill",
            "--name",
            "Dry",
            "--city",
            "Пенза",
            "--dry-run",
            stdout=out,
        )
        assert Tenant.all_objects.get(slug="dry-backfill").city == ""
        assert "[dry-run] would backfill" in out.getvalue()

    def test_overlong_city_is_rejected_before_any_write(self):
        with pytest.raises(CommandError):
            call_command(
                "create_tenant",
                "--slug",
                "long-city",
                "--name",
                "Long",
                "--city",
                "П" * 121,
                stdout=StringIO(),
            )
        assert not Tenant.all_objects.filter(slug="long-city").exists()


class TestCreateTenantMissingIdWarning:
    """The warning must fire where the mistake is still cheap to fix.

    Review finding: it originally printed only after a successful insert, so
    the `--dry-run` preview the runbook mandates — the one step whose whole
    job is catching a forgotten `--id` — showed a clean line and said
    nothing. By the time the warning appeared, the row existed and the fix
    was a delete rather than a flag.
    """

    def test_dry_run_without_id_warns(self):
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "preview-salon",
            "--name",
            "Preview",
            "--city",
            "Пенза",
            "--dry-run",
            stdout=out,
        )
        rendered = out.getvalue()
        assert not Tenant.all_objects.filter(slug="preview-salon").exists()
        assert "no --id given" in rendered
        assert "would create" in rendered

    def test_dry_run_with_id_does_not_warn(self):
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "preview-ok",
            "--name",
            "Preview OK",
            "--id",
            str(uuid.uuid4()),
            "--dry-run",
            stdout=out,
        )
        rendered = out.getvalue()
        assert "would create" in rendered
        assert "no --id given" not in rendered

    def test_system_tenant_without_id_is_not_warned_about(self):
        """`global_bot` and the KB corpus have no Ayla catalog by design.

        Both provisioning recipes in `apps/kb` create one with no `--id`. A
        warning that fires on a documented-correct action is how operators
        learn to scroll past warnings.
        """
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "kb-corpus",
            "--name",
            "KB corpus",
            "--system",
            stdout=out,
        )
        assert Tenant.all_objects.get(slug="kb-corpus").is_system is True
        rendered = out.getvalue()
        assert "Created tenant" in rendered
        assert "no --id given" not in rendered

    def test_empty_id_is_rejected_not_silently_randomised(self):
        """`--id ""` is a flag whose value did not survive its shell.

        Treating it as "not supplied" would mint a random pk — the exact
        silent failure the flag exists to stop.
        """
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "create_tenant",
                "--slug",
                "blank-id",
                "--name",
                "Blank",
                "--id",
                "",
                stdout=StringIO(),
            )
        assert "--id" in str(exc_info.value)
        assert not Tenant.all_objects.filter(slug="blank-id").exists()


class TestCreateTenantExistingRowReporting:
    """The re-run line must describe what happened, not a fixed "No-op".

    Review finding: the old path opened with an unconditional `No-op.` and
    then either raised or performed a write. `No-op.` is a claim about the
    database, and on the backfill path it was false — an operator who typos
    a slug onto an unrelated production tenant was told nothing happened
    while that tenant's `city` was being written.
    """

    def test_plain_rerun_says_no_changes(self):
        call_command("create_tenant", "--slug", "plain", "--name", "Plain", stdout=StringIO())
        out = StringIO()
        call_command("create_tenant", "--slug", "plain", "--name", "Plain", stdout=out)
        rendered = out.getvalue()
        assert "already exists" in rendered
        assert "No changes." in rendered

    def test_backfill_line_does_not_claim_a_no_op(self):
        call_command("create_tenant", "--slug", "backfilled", "--name", "B", stdout=StringIO())
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "backfilled",
            "--name",
            "B",
            "--city",
            "Пенза",
            stdout=out,
        )
        rendered = out.getvalue()
        assert "backfilled blank city" in rendered
        assert "No changes" not in rendered

    def test_blank_city_rerun_points_at_the_two_flags(self):
        """The re-run the runbook uses on the already-connected salons."""
        call_command("create_tenant", "--slug", "no-city", "--name", "NC", stdout=StringIO())
        out = StringIO()
        call_command("create_tenant", "--slug", "no-city", "--name", "NC", stdout=out)
        rendered = out.getvalue()
        assert "city is blank" in rendered
        assert "--city" in rendered
        assert "--id" in rendered

    def test_backfill_bumps_updated_at(self):
        """`updated_at` is `auto_now`; a field left out of `update_fields`
        never has `pre_save` called, so dropping it would freeze the column
        on every backfill and no other assertion would notice."""
        call_command("create_tenant", "--slug", "stamped", "--name", "S", stdout=StringIO())
        before = Tenant.all_objects.get(slug="stamped").updated_at
        call_command(
            "create_tenant",
            "--slug",
            "stamped",
            "--name",
            "S",
            "--city",
            "Пенза",
            stdout=StringIO(),
        )
        after = Tenant.all_objects.get(slug="stamped")
        assert after.city == "Пенза"
        assert after.updated_at > before


class TestCreateTenantCityComparison:
    """Conflict detection must mean what the consumer means.

    `apps.marketplace.discovery` filters `tenant__city__iexact`, so «пенза»
    and «Пенза» are the same city to every client query. Reporting them as
    a conflict would be a scary line about a difference nothing has.
    """

    def test_case_difference_is_not_a_conflict(self):
        call_command(
            "create_tenant",
            "--slug",
            "cased",
            "--name",
            "Cased",
            "--city",
            "Пенза",
            stdout=StringIO(),
        )
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "cased",
            "--name",
            "Cased",
            "--city",
            "пенза",
            stdout=out,
        )
        rendered = out.getvalue()
        # Presence first: a command that printed nothing at all must fail
        # here by name, not slip past the absence assertion below.
        assert "already exists" in rendered
        assert "ignored" not in rendered
        assert Tenant.all_objects.get(slug="cased").city == "Пенза"

    def test_a_real_different_city_still_conflicts(self):
        call_command(
            "create_tenant",
            "--slug",
            "elsewhere",
            "--name",
            "E",
            "--city",
            "Москва",
            stdout=StringIO(),
        )
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "elsewhere",
            "--name",
            "E",
            "--city",
            "Пенза",
            stdout=out,
        )
        assert Tenant.all_objects.get(slug="elsewhere").city == "Москва"
        assert "ignored" in out.getvalue()

    def test_whitespace_only_stored_city_counts_as_blank(self):
        """A city of «   » satisfies no discovery query; it is not "set"."""
        Tenant.objects.create(slug="padded", name="Padded", city="   ")
        call_command(
            "create_tenant",
            "--slug",
            "padded",
            "--name",
            "Padded",
            "--city",
            "Пенза",
            stdout=StringIO(),
        )
        assert Tenant.all_objects.get(slug="padded").city == "Пенза"

    def test_inactive_tenant_is_not_edited_either(self):
        """A deactivated tenant is parked on purpose.

        The command already refuses to resurrect one
        (`test_rerun_with_inactive_tenant_is_noop_not_resurrection`); it must
        not quietly edit one either.
        """
        Tenant.objects.create(slug="parked", name="Parked", is_active=False)
        out = StringIO()
        call_command(
            "create_tenant",
            "--slug",
            "parked",
            "--name",
            "Parked",
            "--city",
            "Пенза",
            stdout=out,
        )
        parked = Tenant.all_objects.get(slug="parked")
        assert parked.city == ""
        assert parked.is_active is False
        assert "inactive" in out.getvalue()
