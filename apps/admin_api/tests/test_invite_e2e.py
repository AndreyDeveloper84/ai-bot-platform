"""End-to-end integration: admin invite → master claim → master accept.

Spans PR 1 (M0 master onboarding) and PR 3 (admin invite, this PR) to
prove the two flows interlock at the wire level.

The single test below:

1. Owner posts to ``POST /api/v1/admin/masters/invite``.
2. The returned ``invite_token`` is fed to
   ``POST /api/v1/master/onboarding/claim`` from the master's BotUser.
3. The same token is posted to
   ``POST /api/v1/master/onboarding/accept``; we verify the master is
   bound to the master's BotUser and ``invite_status=ACCEPTED``.

Why one test, not five: the auth matrix + side-effect coverage lives
in ``test_invite.py`` + ``apps/master_api/tests/test_onboarding.py``.
This test exists ONLY to prove the wire formats match across PR boundaries.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header as admin_header
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.master_api.tests.conftest import _sign as master_sign
from apps.tenancy.models import Tenant


def _master_init_data_header(
    *, bot_token: str, user_id: str = "12345", first_name: str = "Анна"
) -> str:
    import time as time_module

    params = {
        "user": json.dumps({"id": int(user_id), "first_name": first_name}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {master_sign(params, token=bot_token)}"


class TestAdminInviteToMasterAcceptE2E:
    def test_full_loop_admin_invite_then_master_accept(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        # Both apps' init-data signers must agree on MAX_BOT_TOKEN.
        # admin_api.tests.conftest sets MAX_BOT_TOKEN via its autouse
        # fixture; master_api signer must match. We use the same token.
        from apps.admin_api.tests.conftest import BOT_TOKEN as ADMIN_BOT_TOKEN

        # Master's BotUser — the invitee account. NOT linked to any
        # CatalogMaster yet; the accept call binds them.
        invitee = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="12345",
            display_name="Анна Invitee",
            chat_id="12345",
            phone="+79161234567",
        )

        # 1. Owner POSTs the invite.
        #
        # DRF-1349 — the loop is run on a *configured* contour. The invite
        # DM enters the Mini App through an `open_app` button, which needs
        # the bot's Mini App name; with neither that nor a usable
        # SITE_DOMAIN the dispatch now reports `failed` instead of sending
        # a message the invited master could not act on. Leaving both
        # unset here would have this end-to-end test assert `queued`
        # against a contour where nothing can be opened.
        settings.MAX_BOT_WEB_APP = "id583_bot"
        with patch("apps.admin_api.views_invite.max_outbound.send_message") as mock_dm:
            mock_dm.return_value = {"ok": True}
            resp_invite = client.post(
                reverse("admin_api:master_invite_create"),
                data=json.dumps(
                    {
                        "name": "Анна Invitee",
                        "contact_method": "max_username",
                        "contact_value": "@anna_e2e",
                        "schedule_preset": "default_mon_fri_10_19",
                        "mode": "invite",
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=admin_header("5001"),
            )
        assert resp_invite.status_code == 201, resp_invite.content
        invite_body = resp_invite.json()
        token = invite_body["invite_token"]
        master_id = invite_body["master_id"]
        assert token  # non-null
        assert invite_body["max_dm_delivery"] == "queued"

        # 2. Invitee opens the Mini App and calls /onboarding/claim.
        master_header = _master_init_data_header(bot_token=ADMIN_BOT_TOKEN)
        resp_claim = client.post(
            reverse("master_api:onboarding_claim"),
            data=json.dumps({"token": token}),
            content_type="application/json",
            HTTP_AUTHORIZATION=master_header,
        )
        assert resp_claim.status_code == 200, resp_claim.content
        claim_body = resp_claim.json()
        assert claim_body["master"]["id"] == master_id
        assert claim_body["master"]["name"] == "Анна Invitee"

        # 3. Invitee taps «Это я» → /onboarding/accept.
        resp_accept = client.post(
            reverse("master_api:onboarding_accept"),
            data=json.dumps({"token": token}),
            content_type="application/json",
            HTTP_AUTHORIZATION=master_header,
        )
        assert resp_accept.status_code == 200, resp_accept.content
        accept_body = resp_accept.json()
        assert accept_body["master_id"] == master_id
        assert accept_body["session_token"]

        # Verify the master row is bound + ACCEPTED.
        master = CatalogMaster.all_tenants.get(id=master_id)
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert master.linked_bot_user_id == invitee.id
        assert master.invite_token is None  # consumed on accept
