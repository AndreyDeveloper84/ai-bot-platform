"""``manage.py create_tenant`` command tests (DRF-419 / Sprint 1 / A2).

Covers the four behaviours that Operations / CI rely on:

    1. Happy path — argparse parses --slug + --name, tenant is created.
    2. Idempotency — re-running with same slug is a no-op, not an error.
    3. ``--dry-run`` — prints intent, doesn't write to the database.
    4. Slug validation — invalid shapes raise ``CommandError`` (exit code 1)
       *before* hitting the database.
"""

from __future__ import annotations

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
