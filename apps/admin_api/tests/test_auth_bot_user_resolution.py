"""DRF-1150 — the admin surface resolves identity by the bot that signed.

The same MAX account has one ``BotUser`` row per tenant, and the
``TenantStaff`` row lives on exactly one of them. Measured on the pilot
2026-08-16: uid ``83146139`` has a ``global_bot`` row (last_seen 15.08
16:58, no staff) and a ``formula-tela`` row (15.08 09:28, owner). Recency
picks the wrong one; only the tenant filter saved this surface.

That filter came from ``MAX_BOT_TENANT_SLUG`` alone, which is correct
today purely by coincidence — the setting happens to name the salon. With
two bots registered (``MAX_BOTS=client,salon`` on the pilot) the signing
bot is the trustworthy source, and these tests pin that.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import BOT_TOKEN, init_data_header
from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db


def _masters_url() -> str:
    return reverse("admin_api:masters_list")


def _make_bot_user(tenant: Tenant, channel_user_id: str) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name="Карина",
        chat_id=channel_user_id,
    )


class TestBotUserResolution:
    def test_recent_row_in_another_tenant_does_not_win(
        self,
        client: Client,
        tenant: Tenant,
        other_tenant: Tenant,
        owner_bot_user: BotUser,
    ) -> None:
        """The pilot shape: a fresher, role-less row under a second tenant.

        The owner talks to the client bot daily and opens the cabinet
        rarely, so the row WITHOUT the staff link is always the more
        recently seen one.
        """
        stranger = _make_bot_user(other_tenant, "5001")
        BotUser.all_tenants.filter(pk=stranger.pk).update(
            last_seen=owner_bot_user.last_seen + timedelta(days=1)
        )

        resp = client.get(_masters_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200

    def test_signing_bot_tenant_beats_the_setting(
        self,
        client: Client,
        settings,
        tenant: Tenant,
        other_tenant: Tenant,
        owner_bot_user: BotUser,
    ) -> None:
        """The actual fix — the setting points elsewhere, the signature does not.

        Under the previous implementation this answered 404 / 403: the
        lookup was pinned to MAX_BOT_TENANT_SLUG, which here names the
        tenant where this person is nobody.
        """
        # A row for the same MAX account in the tenant the SETTING names.
        _make_bot_user(other_tenant, "5001")
        settings.MAX_BOT_TENANT_SLUG = other_tenant.slug
        # ...but the bot whose token signs the initData serves `tenant`.
        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="salon",
                # Left empty on purpose — this surface never touches the
                # webhook gate, only the api_token that verifies initData.
                webhook_secret="",
                api_token=BOT_TOKEN,
                tenant_slug=tenant.slug,
            ),
        )

        resp = client.get(_masters_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200

    def test_no_tenant_configured_is_a_loud_500(
        self,
        client: Client,
        settings,
        tenant: Tenant,
        owner_bot_user: BotUser,
    ) -> None:
        """A deployment with neither source configured must fail loudly."""
        settings.MAX_BOT_TENANT_SLUG = ""
        settings.MAX_BOT_REGISTRY = ()

        resp = client.get(_masters_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 500
        assert resp.json()["error"] == "server_misconfigured"

    def test_unknown_account_still_404s(
        self, client: Client, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        resp = client.get(_masters_url(), HTTP_AUTHORIZATION=init_data_header("9999"))
        assert resp.status_code == 404
        assert resp.json()["error"] == "user_not_registered"

    def test_role_is_read_from_the_resolved_rows_own_tenant(
        self,
        client: Client,
        settings,
        tenant: Tenant,
        other_tenant: Tenant,
    ) -> None:
        """A wrong pick can only under-privilege, never over-privilege.

        The staff row sits in `other_tenant`; the signing bot serves
        `tenant`, where this account holds nothing. Resolving to the
        `tenant` row must yield 403 — the role is read from that row's own
        tenant, so a mis-resolution cannot borrow someone else's owner
        rights.
        """
        _make_bot_user(tenant, "5007")
        elsewhere = _make_bot_user(other_tenant, "5007")
        TenantStaff.all_tenants.create(
            tenant=other_tenant, bot_user=elsewhere, role=TenantStaff.Role.OWNER
        )
        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="salon",
                # Left empty on purpose — this surface never touches the
                # webhook gate, only the api_token that verifies initData.
                webhook_secret="",
                api_token=BOT_TOKEN,
                tenant_slug=tenant.slug,
            ),
        )

        resp = client.get(_masters_url(), HTTP_AUTHORIZATION=init_data_header("5007"))
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"
