"""DRF-2787 — /start of the salon bot: the bot's own actions first.

Owner's complaint 05.10 (buttons3.png): the greeting carried only ``open_app``
buttons — «Открыть салон / Сегодня / Расписание / Спросить Ayla / ＋ Новая
запись» — so everything the bot can do itself sat hidden in the menu. Now
the chat actions come first and the Mini App screens stay beside them.

Through ``handle_salon_max_event`` with «/start»; the wire's buttons are read
(text and payload), labels as literals.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite, redeem_staff_invite
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import StaffInvite, Tenant

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "702787"

WITH_APP = BotEntry(
    slug="salon",
    webhook_secret="wh",  # pragma: allowlist secret
    api_token="tok-salon",  # pragma: allowlist secret
    tenant_slug="greet-salon",
    stream="max_salon",
    web_app="id583_salon_bot",
)
WITHOUT_APP = BotEntry(
    slug="salon",
    webhook_secret="wh",  # pragma: allowlist secret
    api_token="tok-salon",  # pragma: allowlist secret
    tenant_slug="greet-salon",
    stream="max_salon",
)


@pytest.fixture(autouse=True)
def _env(settings, monkeypatch):
    monkeypatch.setattr("apps.channels.max.salon_entry.unlinked_reason", lambda *a, **kw: "")
    settings.MAX_BOT_REGISTRY = (WITH_APP,)
    settings.MAX_BOT_TOKEN = "tok-client"  # pragma: allowlist secret


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="greet-salon", defaults={"name": "Формула", "timezone": "Europe/Moscow"}
    )
    return obj


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


def _admin(tenant, *, greeted: bool = True) -> BotUser:
    person = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID
    )
    _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
    redeem_staff_invite(code=code, bot_user=person, tenant=tenant)
    if greeted:
        # Not the first entry: the ordinary greeting with the day's summary.
        BotUser.all_tenants.filter(pk=person.pk).update(welcomed_at=timezone.now())
    return person


def _master(tenant) -> BotUser:
    person = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID
    )
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Анна",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=person,
    )
    return person


def _event(text: str, *, callback: bool = False) -> dict[str, Any]:
    if callback:
        return {
            "update_type": "message_callback",
            "timestamp": 1_700_000_000_000,
            "callback": {
                "callback_id": f"cb-{uuid.uuid4().hex[:8]}",
                "payload": text,
                "timestamp": 1_700_000_000_000,
                "user": {"user_id": int(CHANNEL_USER_ID), "name": "Владелец"},
            },
            "message": {
                "body": {"mid": "m1", "seq": 1, "text": ""},
                "sender": {"user_id": 999, "name": "bot", "is_bot": True},
                "recipient": {"chat_id": 555, "user_id": 999, "chat_type": "dialog"},
            },
        }
    return {
        "update_type": "message_created",
        "timestamp": 1_700_000_000_000,
        "message": {
            "sender": {"user_id": int(CHANNEL_USER_ID), "name": "Владелец", "is_bot": False},
            "recipient": {"chat_id": 555, "user_id": 999, "chat_type": "dialog"},
            "body": {
                "mid": f"mid-{uuid.uuid4().hex[:8]}",
                "seq": 1,
                "text": text,
                "attachments": [],
            },
        },
    }


def _send(tenant, text: str, *, callback: bool = False) -> None:
    from apps.channels.max.salon_handler import handle_salon_max_event

    with tenant_scope(tenant):
        handle_salon_max_event(_event(text, callback=callback))


def _buttons(sent) -> list[tuple[str, str, bool]]:
    """(label, payload, is_mini_app) in wire order."""

    out: list[tuple[str, str, bool]] = []
    for attachment in sent.call_args.kwargs.get("attachments") or []:
        for row in (attachment.get("payload") or {}).get("buttons") or []:
            for b in row:
                out.append((b.get("text", ""), b.get("payload", ""), b.get("type") != "callback"))
    return out


ADMIN_CHAT = [
    ("📅 Сегодня", "cb:staff:day", False),
    ("🗒 Заявки от мастеров", "cb:staff:requests", False),
    ("✍️ Записать клиента", "cb:staff:bk_new", False),
    ("Проверить готовность", "cb:staff:readiness", False),
]


class TestOwnerGreeting:
    def test_g1_chat_actions_first_then_the_mini_app_beside_them(self, tenant, sent):
        _admin(tenant)

        _send(tenant, "/start")

        buttons = _buttons(sent)
        assert buttons[:4] == ADMIN_CHAT
        assert [label for label, _, app in buttons[4:]] == [
            "Открыть салон",
            "Расписание",
            "Спросить Ayla",
            "＋ Новая запись",
        ]
        assert all(app for _, _, app in buttons[4:])
        # One «Сегодня»: the day is the first chat button, not a second screen.
        assert sum(1 for label, _, _ in buttons if "Сегодня" in label) == 1

    def test_g2_without_a_mini_app_the_bot_still_works_from_start(self, tenant, sent, settings):
        settings.MAX_BOT_REGISTRY = (WITHOUT_APP,)
        _admin(tenant)

        _send(tenant, "/start")

        assert _buttons(sent) == ADMIN_CHAT

    def test_g3_after_readiness_the_same_set(self, tenant, sent):
        _admin(tenant)

        _send(tenant, "cb:staff:readiness", callback=True)

        assert _buttons(sent)[:4] == ADMIN_CHAT


class TestMasterGreeting:
    def test_g4_my_day_in_the_chat_first(self, tenant, sent):
        _master(tenant)

        _send(tenant, "/start")

        buttons = _buttons(sent)
        assert buttons[0] == ("📅 Мой день", "cb:staff:day", False)
        assert [label for label, _, app in buttons[1:]] == [
            "Открыть кабинет",
            "Расписание",
            "Спросить Ayla",
        ]
        assert all(app for _, _, app in buttons[1:])
