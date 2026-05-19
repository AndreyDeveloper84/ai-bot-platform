"""Tests for the create_test_master_invite management command."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="ma-cmd-test",
        name="MA Command Test Salon",
        timezone="Europe/Moscow",
    )


def _run(**kwargs: object) -> str:
    """Invoke the command and return stdout as a string."""

    out = StringIO()
    call_command("create_test_master_invite", stdout=out, **kwargs)
    return out.getvalue()


class TestCreateTestMasterInvite:
    def test_happy_path_creates_pending_master(self, tenant: Tenant) -> None:
        out = _run(tenant=tenant.slug, name="Анна Петрова", max_handle="anna_styl")

        master = CatalogMaster.all_tenants.get(tenant=tenant, name="Анна Петрова")
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert master.invite_token is not None
        assert master.invite_expires_at is not None
        # ~7d expiry, allow a few seconds of clock skew.
        delta = master.invite_expires_at - timezone.now()
        assert timedelta(days=6, hours=23) < delta <= timedelta(days=7, minutes=1)
        assert master.mode == CatalogMaster.Mode.INVITE
        assert master.is_active is True
        assert master.max_handle == "anna_styl"

        # Output mentions the token + a web URL.
        assert "invite_token:" in out
        assert "deeplink:" in out
        assert str(master.invite_token) in out
        assert "/onboarding/master?token=" in out

    def test_idempotent_reuses_existing_pending(self, tenant: Tenant) -> None:
        _run(tenant=tenant.slug, name="Анна Петрова")
        first = CatalogMaster.all_tenants.get(tenant=tenant, name="Анна Петрова")
        first_token = first.invite_token

        out = _run(tenant=tenant.slug, name="Анна Петрова")

        # Same row, same token — no duplicate rows.
        assert CatalogMaster.all_tenants.filter(tenant=tenant, name="Анна Петрова").count() == 1
        first.refresh_from_db()
        assert first.invite_token == first_token
        assert "Reusing" in out

    def test_regenerate_rotates_token_and_extends_expiry(self, tenant: Tenant) -> None:
        _run(tenant=tenant.slug, name="Анна Петрова")
        first = CatalogMaster.all_tenants.get(tenant=tenant, name="Анна Петрова")
        first_token = first.invite_token

        _run(tenant=tenant.slug, name="Анна Петрова", regenerate=True)

        first.refresh_from_db()
        assert first.invite_token != first_token
        assert first.invite_expires_at is not None
        delta = first.invite_expires_at - timezone.now()
        assert delta > timedelta(days=6, hours=23)

    def test_unknown_tenant_raises(self, db) -> None:
        with pytest.raises(CommandError, match="not found"):
            _run(tenant="does-not-exist", name="Анна Петрова")

    def test_blank_name_raises(self, tenant: Tenant) -> None:
        with pytest.raises(CommandError, match="must not be blank"):
            _run(tenant=tenant.slug, name="   ")
