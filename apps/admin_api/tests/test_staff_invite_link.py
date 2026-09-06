"""An access code must be forwardable, not dictated aloud (DRF-1505).

``staff/invite/`` produced four characters — ``AYLA-7K3M`` — and nothing
else. The only way to hand them over was to read them to somebody, or
retype them into a messenger, and both fail the way short codes always
fail: ``0``/``O``, ``1``/``I``, a letter heard wrong over a phone in a
salon. The alphabet already excludes the worst confusions
(:data:`apps.identity.services.staff_invites.CODE_ALPHABET`), which is a
mitigation, not a delivery mechanism.

``invite_link`` is the same credential in a form that can be pasted:
``https://max.ru/<salon bot>?start=inv_<code>``. Nothing new happens on
redemption — MAX delivers ``?start=`` as ``bot_started.payload``, the
parser folds it into «/start inv_<code>», and
``salon_handler._extract_code`` has read that shape since DRF-1061. The
tests below are about the parts that can be wrong while looking right:
**which bot the link names**, and **that the payload is the same code**.

Paired with ``apps/channels/tests/test_salon_invite_entry.py``, which
holds the other end of the same seam.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: Shaped like the pilot's real salon bot (``id583403546770_3_bot``),
#: which is both its Mini App name and its public handle.
SALON_WEB_APP = "id583403546770_3_bot"

#: Declared FIRST on purpose. Tenant alone does not identify a bot, and a
#: link built from whichever entry came first would name this one —
#: delivering the code to ``ingress:max``, where no handler redeems it.
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

MUTE_SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="admin-api-test",
    stream="max_salon",
)


def _issue(client: Client, *, registry: tuple[BotEntry, ...], settings) -> dict[str, Any]:
    """Issue one receptionist code and return the response body."""

    settings.MAX_BOT_REGISTRY = registry
    resp = client.post(
        reverse("admin_api:staff_invite_create"),
        data={"role": "receptionist"},
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )
    assert resp.status_code == 201, resp.content
    return resp.json()


class TestTheCodeCanBeHandedOver:
    def test_the_response_carries_a_start_link_for_the_code(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        from apps.channels.max.salon_handler import DEEPLINK_PREFIX

        body = _issue(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        flat = body["code"].replace("-", "")
        assert body["invite_link"] == (
            f"https://max.ru/{SALON_WEB_APP}?start={DEEPLINK_PREFIX}{flat}"
        )

    def test_the_link_carries_the_very_code_that_is_shown(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """Two strings the reader is told are the same credential.

        Compared through ``normalize_code`` rather than as raw text: the
        screen shows ``AYLA-7K3M`` and the link carries ``AYLA7K3M``,
        which is the shape ``manage.py issue_staff_invite`` has printed
        since DRF-1061. Both fold to the same four characters, and that
        folding is the thing worth pinning — if the link ever carried a
        *different* code, the screen would show one that works next to a
        link that does not, and the person who followed the link would
        be told their invitation is invalid.
        """

        from apps.identity.services.staff_invites import normalize_code

        body = _issue(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        payload = body["invite_link"].split("?start=inv_", 1)[1]
        assert body["code"]
        assert normalize_code(payload) == normalize_code(body["code"])

    def test_the_payload_in_the_link_is_still_redeemable(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """The bot's own reader must accept what the link carries.

        Reading the link back through ``_extract_code`` closes the loop
        the way the runtime does: MAX turns ``?start=X`` into the
        synthetic text «/start X», and that text is what the handler
        sees. A test that only checked the URL's shape would stay green
        through a change to either side of the prefix.
        """

        from apps.channels.max.salon_handler import _extract_code
        from apps.identity.services.staff_invites import normalize_code

        body = _issue(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        payload = body["invite_link"].split("?start=", 1)[1]
        extracted = _extract_code(f"/start {payload}")

        assert extracted is not None
        assert normalize_code(extracted) == normalize_code(body["code"])

    def test_the_link_names_the_salon_bot_not_the_client_bot(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        body = _issue(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        assert SALON_WEB_APP in body["invite_link"]
        assert CLIENT_BOT.web_app not in body["invite_link"]

    def test_the_link_is_not_the_dead_max_scheme(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """``max://`` is unimplemented — #1332 removed it after the owner
        followed one and got «Не удалось открыть ссылку».

        Paired with a positive claim on the same value, because «does not
        start with max://» is green for an empty string too.
        """

        body = _issue(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        assert not body["invite_link"].startswith("max://")
        assert body["invite_link"].startswith("https://max.ru/")


class TestNoLinkRatherThanADeadOne:
    def test_no_salon_bot_means_no_link(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        body = _issue(client, registry=(CLIENT_BOT,), settings=settings)

        assert body["invite_link"] == ""

    def test_a_salon_bot_without_a_mini_app_name_means_no_link(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        body = _issue(client, registry=(CLIENT_BOT, MUTE_SALON_BOT), settings=settings)

        assert body["invite_link"] == ""

    def test_the_code_itself_survives_a_missing_link(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """A contour with no salon bot still issues a usable credential.

        The link is a convenience on top of the code, and it must fail
        alone. If a missing ``web_app`` ever started costing the issuer
        the code as well, the screen would answer «не получилось выдать
        код» to a request that in fact created a live invite row.
        """

        body = _issue(client, registry=(CLIENT_BOT,), settings=settings)

        assert body["code"]
        assert body["code_is_shown_once"] is True

    def test_the_same_call_with_a_configured_bot_does_produce_one(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """Positive guard for both empty-string cases above."""

        body = _issue(client, registry=(CLIENT_BOT, SALON_BOT), settings=settings)

        assert body["invite_link"]
