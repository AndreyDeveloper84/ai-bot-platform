"""The invitation must be handable over, not only DM-able (DRF-1424).

#1332 gave the invitation a working entry — an ``open_app`` button — but
only inside a DM, and :func:`~apps.admin_api.views_invite._dispatch_max_dm`
can address that DM only to a MAX username the salon already knows, in a
chat that already exists. An owner holding a phone number, a Telegram
handle, or a group chat has nothing to send.

``invite_link`` is that missing object: a start link
(``https://max.ru/<bot>?start=master_invite_<token>``) which opens
anywhere and needs no authentication to follow. Opening it fires
``bot_started`` carrying the token as ``payload`` — verified live on the
pilot 30.08, stream ``ingress:max_salon`` — and the salon bot answers
into a chat that now exists, which is what makes the button's delivery
guaranteed rather than hopeful
(``apps/channels/tests/test_salon_invite_entry.py`` is the other end).

The assertions here are about **which bot the link names**, because that
is the part that can be wrong while looking right: only
``ingress:max_salon`` reaches the handler that reads invitations, so a
link naming the customer-facing bot would deliver the token to a
pipeline with no opinion about it and drop it silently.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: The salon bot's Mini App name — which is also its public handle, and
#: therefore the ``<bot>`` in the start link. Shaped like the pilot's
#: real one (``id583403546770_3_bot``).
SALON_WEB_APP = "id583403546770_3_bot"

#: A per-tenant *client* bot, present on purpose. Tenant alone does not
#: identify a bot, and a link built from whichever entry came first would
#: name this one — sending the token to the conversational pipeline,
#: where nothing reads it.
CLIENT_BOT = BotEntry(
    slug="client",
    webhook_secret="wh-client",  # pragma: allowlist secret
    api_token="token-client",  # pragma: allowlist secret
    tenant_slug="admin-api-test",
    stream="max",
    web_app="client_bot",
)

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="admin-api-test",
    stream="max_salon",
    web_app=SALON_WEB_APP,
)


def _invite_url() -> str:
    return reverse("admin_api:master_invite_create")


def _valid_body() -> dict[str, Any]:
    return {
        "name": "Анна Петрова",
        "contact_method": "max_username",
        "contact_value": "@anna_styl",
        "services": [],
        "schedule_preset": "default_mon_fri_10_19",
        "mode": "invite",
    }


def _invite(client: Client, *, registry: tuple[BotEntry, ...], settings) -> dict[str, Any]:
    """Create one invitation and return the response body."""

    settings.MAX_BOT_REGISTRY = registry
    settings.MAX_BOT_WEB_APP = SALON_WEB_APP
    settings.SITE_DOMAIN = "https://miniapp-dev.example"
    with patch("apps.admin_api.views_invite.max_outbound.send_message") as mock:
        mock.return_value = {"ok": True}
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
    assert resp.status_code == 201, resp.content
    return resp.json()


class TestTheOwnerGetsSomethingToHandOver:
    def test_the_response_carries_a_start_link(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        from apps.admin_api.views_invite import MASTER_INVITE_PAYLOAD_PREFIX

        body = _invite(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        token = body["invite_token"]
        assert body["invite_link"] == (
            f"https://max.ru/{SALON_WEB_APP}?start={MASTER_INVITE_PAYLOAD_PREFIX}{token}"
        )

    def test_the_link_names_the_salon_bot_not_the_client_bot(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """The registry declares the client bot first, on purpose.

        Matching on tenant alone would return it, and the resulting
        ``bot_started`` would land on ``ingress:max``, where no handler
        reads invitations. The token would arrive and vanish — the exact
        silence this ticket exists to remove.
        """

        body = _invite(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        assert SALON_WEB_APP in body["invite_link"]
        assert CLIENT_BOT.web_app not in body["invite_link"]

    def test_the_link_is_not_the_dead_max_scheme(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """``max://`` is unimplemented — #1332 removed it after the owner
        followed it and got «Не удалось открыть ссылку».

        Paired with a positive claim on the same value, because «does not
        start with max://» is green for an empty string too.
        """

        body = _invite(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        assert not body["invite_link"].startswith("max://")
        assert body["invite_link"].startswith("https://max.ru/")

    def test_the_payload_in_the_link_is_a_flat_slug(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """Only ONE ``?`` in the whole URL — the one starting the query.

        The value after ``?start=`` is echoed back by the bot into an
        ``open_app`` button, and MAX answers a payload containing ``=``,
        ``&`` or ``?`` with HTTP 400 ``proto.payload`` (Guard 3 in
        ``apps/channels/max/outbound.py``). A second parameter appended
        here would make every opened invitation poison the consumer.
        """

        body = _invite(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        link = body["invite_link"]
        payload = link.split("?start=", 1)[1]
        assert not set("=&?") & set(payload), payload


class TestNoLinkRatherThanADeadOne:
    """A missing link is a visible gap; a dead one wastes the invitee's try."""

    def test_no_salon_bot_means_no_link(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        body = _invite(client, registry=(CLIENT_BOT,), settings=settings)

        assert body["invite_link"] == ""

    def test_a_salon_bot_without_a_mini_app_name_means_no_link(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """Without ``web_app`` the bot could not build the button either.

        So the link would open a conversation whose only possible answer
        is an apology — worse than nothing, because the invitee spends
        their attempt on it.
        """

        mute = BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token="token-salon",  # pragma: allowlist secret
            tenant_slug="admin-api-test",
            stream="max_salon",
        )

        body = _invite(client, registry=(CLIENT_BOT, mute), settings=settings)

        assert body["invite_link"] == ""

    def test_the_same_call_with_a_configured_bot_does_produce_one(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """Positive guard for both cases above, through the same helper."""

        body = _invite(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        assert body["invite_link"]
