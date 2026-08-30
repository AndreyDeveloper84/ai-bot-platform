"""Tests for the create_test_master_invite management command."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.admin_api.views_invite import MASTER_INVITE_PAYLOAD_PREFIX
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

        # Output mentions the token, the open_app payload and a web URL.
        #
        # DRF-1349 — this used to require a `deeplink:` line carrying a
        # `max://bot/…` address. MAX does not implement that scheme, so
        # the line taught whoever tested by hand the exact dead form the
        # invite itself was shipping. What the real DM carries, and what
        # the Mini App resolves, is the `open_app` payload.
        assert "invite_token:" in out
        assert "open_app payload:" in out
        assert f"{MASTER_INVITE_PAYLOAD_PREFIX}{master.invite_token}" in out
        assert "max://" not in out
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

    def test_bootstrap_bot_user_creates_placeholder_and_prints_env(self, tenant: Tenant) -> None:
        """``--bootstrap-bot-user`` pre-creates a BotUser and prints .env snippet."""
        from apps.identity.models import BotUser

        out = _run(
            tenant=tenant.slug,
            name="Анна Петрова",
            bootstrap_bot_user=True,
        )
        master = CatalogMaster.all_tenants.get(tenant=tenant, name="Анна Петрова")
        bot_user = BotUser.all_tenants.get(
            tenant=tenant,
            channel="max",
            channel_user_id=f"dev-bypass-{master.id}",
        )
        # Snippet rendered with placeholder bot_user.id.
        assert "VITE_DEV_BYPASS_USER_ID=" in out
        assert str(bot_user.id) in out
        assert f"VITE_DEV_BYPASS_TENANT_SLUG={tenant.slug}" in out
        # Follow-up hint for after M0 acceptance.
        assert "print_master_dev_env" in out


class TestPrintMasterDevEnv:
    """Cover the print_master_dev_env command's happy path + error cases."""

    def test_happy_path_prints_env_snippet(self, tenant: Tenant) -> None:
        from datetime import datetime, timezone as dt_tz

        from apps.identity.models import BotUser

        now = datetime.now(tz=dt_tz.utc)
        bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="m-99001",
            display_name="Master",
            chat_id="m-99001",
        )
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=12345,
            external_updated_at=now,
            name="Linked Master",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            linked_bot_user=bu,
        )
        out = StringIO()
        call_command("print_master_dev_env", str(master.id), stdout=out)
        text = out.getvalue()
        assert f"VITE_DEV_BYPASS_USER_ID={bu.id}" in text
        assert f"VITE_DEV_BYPASS_TENANT_SLUG={tenant.slug}" in text
        assert "DEBUG=True" in text

    def test_master_not_linked_raises(self, tenant: Tenant) -> None:
        from datetime import datetime, timezone as dt_tz
        import uuid

        now = datetime.now(tz=dt_tz.utc)
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=12346,
            external_updated_at=now,
            name="Unlinked Master",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.PENDING,
            invite_token=uuid.uuid4(),
            invited_at=now,
        )
        out = StringIO()
        with pytest.raises(CommandError, match="no linked_bot_user"):
            call_command("print_master_dev_env", str(master.id), stdout=out)

    def test_invalid_uuid_raises(self, db) -> None:
        out = StringIO()
        with pytest.raises(CommandError, match="not a valid UUID"):
            call_command("print_master_dev_env", "not-a-uuid", stdout=out)

    def test_missing_master_raises(self, db) -> None:
        import uuid

        out = StringIO()
        with pytest.raises(CommandError, match="not found"):
            call_command("print_master_dev_env", str(uuid.uuid4()), stdout=out)
