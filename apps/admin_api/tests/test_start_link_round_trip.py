"""The whole pass, end to end, in one process (DRF-1505).

Every piece of this chain already had a test, and the chain still did not
work: the backend built ``invite_link`` (DRF-1424), the bot read
``?start=`` payloads (DRF-1424 / DRF-1061), and the Mini App's response
type had no ``invite_link`` field at all — so the one delivery route that
works never reached the person who had to use it. Thirty of thirty-four
pilot masters could be booked by clients and could not open their own
cabinet.

That is the failure mode this file is aimed at: **each half green,
nothing joined.** So nothing here is restated. The link is taken from the
admin endpoint's own response, split the way MAX splits it, and fed to
the salon handler as the ``bot_started`` event MAX actually sends
(field-for-field from the pilot snapshot on 30.08). What comes back is
read out of the outbound call.

What this cannot cover, and where it stops being enough: the phone. MAX
turning a ``max.ru`` URL into ``bot_started`` is observed behaviour of a
third party, not of this repository — it was verified live on the pilot
30.08 and is recorded in
:data:`apps.channels.max.start_links.MAX_START_LINK_TEMPLATE`. Everything
downstream of that observation is here.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.admin_api.tests.conftest import init_data_header
from apps.channels.bot_registry import BotEntry
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

SALON_WEB_APP = "id583403546770_3_bot"

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="admin-api-test",
    stream="max_salon",
    web_app=SALON_WEB_APP,
)

#: The stranger. Not the owner, not any master: a MAX user id this
#: contour has never seen, which is the whole point — the DM route can
#: only reach a chat that already exists, and this person has none.
STRANGER_USER_ID = "83146139"
STRANGER_CHAT_ID = "315714313"

_next_offset = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT,)
    settings.MAX_BOT_WEB_APP = SALON_WEB_APP
    settings.SITE_DOMAIN = "https://miniapp-dev.example"
    settings.DEBUG = False


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


def _start_payload(link: str) -> str:
    """What MAX delivers as ``bot_started.payload`` for this link.

    Split rather than rebuilt: if the producer ever appends a second
    query parameter, this takes the whole tail and the handler refuses
    it — which is the behaviour under test, not something to route
    around by parsing more cleverly than MAX does.
    """

    assert link, "no link to follow — the producer returned an empty string"
    return link.split("?start=", 1)[1]


def _bot_started(payload: str) -> dict[str, Any]:
    """One ``bot_started`` update, shaped as MAX actually sends it.

    Only the timestamp moves: the parser derives ``channel_message_id``
    from ``user_id`` plus timestamp and the handler's idempotency claim
    lives 24h, so two events sharing a timestamp would make the second
    vanish for reasons unrelated to what is under test.
    """

    return {
        "update_type": "bot_started",
        "timestamp": int(timezone.now().timestamp() * 1000) + next(_next_offset),
        "chat_id": int(STRANGER_CHAT_ID),
        "user": {
            "user_id": int(STRANGER_USER_ID),
            "first_name": "Незнакомец",
            "last_name": "",
            "is_bot": False,
            "name": "Незнакомец",
        },
        "user_locale": "ru",
        "payload": payload,
    }


def _follow(tenant: Tenant, link: str) -> None:
    """Open the link, as the invited person does on their phone."""

    with tenant_scope(tenant):
        handle_salon_max_event(_bot_started(_start_payload(link)))


def _buttons(mock) -> list[dict[str, Any]]:
    assert mock.called, "the bot answered nothing at all"
    out: list[dict[str, Any]] = []
    for attachment in mock.call_args.kwargs.get("attachments") or []:
        for row in attachment.get("payload", {}).get("buttons", []):
            out.extend(row)
    return out


def _text(mock) -> str:
    assert mock.called, "the bot answered nothing at all"
    return mock.call_args.kwargs["text"]


def _invite_master(client: Client) -> dict[str, Any]:
    # Никакой подмены исходящего: эндпоинт приглашения с §44.4 не шлёт
    # ничего сам. Что он молчит — стережёт ``test_invite_no_dm.py``.
    resp = client.post(
        reverse("admin_api:master_invite_create"),
        data={
            "name": "Анна Петрова",
            "contact_method": "max_username",
            "contact_value": "@anna_styl",
            "services": [],
            "schedule_preset": "default_mon_fri_10_19",
            "mode": "invite",
        },
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )
    assert resp.status_code == 201, resp.content
    return resp.json()


def _issue_code(client: Client, role: str = "receptionist") -> dict[str, Any]:
    resp = client.post(
        reverse("admin_api:staff_invite_create"),
        data={"role": role},
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )
    assert resp.status_code == 201, resp.content
    return resp.json()


class TestTheMasterInvitationTravelsTheWholeWay:
    def test_a_stranger_who_opens_the_link_is_handed_the_way_in(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, sent
    ):
        """Owner creates → owner copies → stranger opens → button.

        The stranger has no chat with any bot of ours and no ``BotUser``
        row, which is exactly why the DM route cannot reach them. This
        is the route that can.
        """

        body = _invite_master(client)

        _follow(tenant, body["invite_link"])

        payload = f"master_invite_{body['invite_token']}"
        buttons = [b for b in _buttons(sent) if b.get("payload") == payload]
        assert buttons, (
            f"no open_app button carrying {payload}; the bot replied "
            f"{_text(sent)!r} with buttons {_buttons(sent)!r}"
        )
        assert buttons[0]["type"] == "open_app"
        assert buttons[0]["web_app"] == SALON_WEB_APP

    def test_opening_it_does_not_spend_the_invitation(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, sent
    ):
        """A forwarded link must not burn the invitation.

        Opening a link proves possession of the link and nothing else:
        ``bot_started`` carries a user id but no MAX username, so there
        is no way here to tell the invitee from whoever was forwarded
        the message. Binding on this event would let a stranger consume
        a master's row.

        Paired with a positive claim on the same rows: the invitation is
        still PENDING **and** the button did go out. «Nothing was
        consumed» is green against a handler that did nothing.
        """

        from apps.catalog.models import CatalogMaster

        body = _invite_master(client)

        _follow(tenant, body["invite_link"])

        master = CatalogMaster.all_tenants.get(id=body["master_id"])
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert master.linked_bot_user_id is None
        assert _buttons(sent), "nothing was sent — the PENDING check proves nothing"

    def test_a_crafted_tail_is_refused_rather_than_echoed(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, sent
    ):
        """A start link is public: whatever a stranger types arrives here.

        ``master_invite_<uuid>?src=x`` must not become an ``open_app``
        payload — MAX answers a payload containing ``?``/``=``/``&`` with
        HTTP 400 ``proto.payload``, and that error lands on the consumer,
        not on whoever crafted the link.
        """

        body = _invite_master(client)

        _follow(tenant, f"{body['invite_link']}?src=phish")

        assert _text(sent), "the bot must still answer something"
        assert not [
            b for b in _buttons(sent) if str(b.get("payload", "")).startswith("master_invite_")
        ]


class TestTheAccessCodeTravelsTheWholeWay:
    def test_following_the_link_grants_the_role_without_typing(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, sent
    ):
        """Owner issues → owner pastes → person opens → access.

        The pairing that matters here is with
        ``test_typing_the_code_still_works`` below: the link is a second
        door onto the same redemption, and the first one must stay open.
        """

        body = _issue_code(client)

        _follow(tenant, body["invite_link"])

        granted = TenantStaff.all_tenants.filter(
            tenant=tenant,
            bot_user__channel_user_id=STRANGER_USER_ID,
            role=TenantStaff.Role.RECEPTIONIST,
        )
        assert granted.exists(), f"the code was not redeemed; the bot replied {_text(sent)!r}"

    def test_typing_the_code_still_works(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, sent
    ):
        """The paired positive: the path that existed before is intact.

        The link changes how the code reaches a phone, nothing else. If
        this ever goes red, the salon's staff cannot get in at all — the
        typed code is the fallback for every contour that has no salon
        bot to build a link from.
        """

        body = _issue_code(client)

        with tenant_scope(tenant):
            handle_salon_max_event(
                {
                    "update_type": "message_created",
                    "timestamp": int(timezone.now().timestamp() * 1000) + next(_next_offset),
                    "message": {
                        "sender": {"user_id": int(STRANGER_USER_ID), "name": "Незнакомец"},
                        "recipient": {"chat_id": int(STRANGER_CHAT_ID), "chat_type": "dialog"},
                        "timestamp": int(timezone.now().timestamp() * 1000),
                        "body": {"mid": str(uuid.uuid4()), "seq": 1, "text": body["code"]},
                    },
                }
            )

        assert TenantStaff.all_tenants.filter(
            tenant=tenant,
            bot_user__channel_user_id=STRANGER_USER_ID,
            role=TenantStaff.Role.RECEPTIONIST,
        ).exists(), f"typed code refused; the bot replied {_text(sent)!r}"

    def test_the_link_is_single_use_like_the_code(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, sent
    ):
        """Following it twice must not hand out a second role.

        The link IS the code, and the code is one-shot. The screen says
        so; this is the half that has to be true.
        """

        body = _issue_code(client)

        _follow(tenant, body["invite_link"])
        _follow(tenant, body["invite_link"])

        rows = TenantStaff.all_tenants.filter(
            tenant=tenant,
            bot_user__channel_user_id=STRANGER_USER_ID,
            role=TenantStaff.Role.RECEPTIONIST,
        )
        assert rows.count() == 1
