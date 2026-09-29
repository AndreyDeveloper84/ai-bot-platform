"""DRF-2665: an empty Telegram user id is not a person (the MAX door: DRF-2661).

``"id" in sender`` holds for ``{"id": null}`` and ``str(None)`` is ``"None"``:
every such update would resolve to ONE person. Reachability today is nil —
behind Telegram's secret token, and Telegram always sends an integer — but
two doors with one purpose must follow one rule.

The node is the merge, through the same resolver the Telegram handler uses:
two updates with an empty id from two chats must not become one person; two
real ids must become two. This module refuses by ``None`` (see the parser
docstring, «Why return None»), and the handler returns on ``None``.
"""

from __future__ import annotations

import copy

import pytest

from apps.channels.telegram.parser import parse_inbound
from apps.identity.models import BotUser
from apps.identity.services.resolver import resolve_or_create_bot_user
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

MESSAGE = {
    "update_id": 100001,
    "message": {
        "message_id": 42,
        "date": 1731320000,
        "from": {"id": 12345, "is_bot": False, "first_name": "Иван"},
        "chat": {"id": 12345, "type": "private"},
        "text": "Привет",
    },
}
CALLBACK = {
    "update_id": 100002,
    "callback_query": {
        "id": "cq-1",
        "from": {"id": 12345, "is_bot": False, "first_name": "Иван"},
        "message": {
            "message_id": 99,
            "date": 1731320002,
            "chat": {"id": 12345, "type": "private"},
            "text": "(button row)",
        },
        "data": "cb:rem:confirm:abc",
        "chat_instance": "ci-1",
    },
}


def _with(template: dict, user_id, chat_id: int) -> dict:
    payload = copy.deepcopy(template)
    body = payload["message"] if "message" in payload else payload["callback_query"]
    body["from"]["id"] = user_id
    chat = body["chat"] if "chat" in body else body["message"]["chat"]
    chat["id"] = chat_id
    return payload


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="tg-2665", name="TG")


def _person_of(payload: dict, tenant: Tenant) -> str | None:
    """What the handler does: parse (``None`` → return), then resolve."""

    event = parse_inbound(payload)
    if event is None:
        return None
    with tenant_scope(tenant):
        return str(
            resolve_or_create_bot_user(
                channel="telegram", channel_user_id=event.channel_user_id, chat_id=event.chat_id
            ).pk
        )


class TestTwoEmptyIdsAreNotOnePerson:
    @pytest.mark.parametrize("template", [MESSAGE, CALLBACK], ids=["message", "callback_query"])
    def test_two_empty_ids_from_two_chats_become_nobody(self, template, tenant) -> None:
        people = {
            _person_of(_with(template, None, 1001), tenant),
            _person_of(_with(template, None, 1002), tenant),
        }

        assert people == {None}
        assert not BotUser.all_tenants.filter(channel_user_id="None").exists()

    @pytest.mark.parametrize("template", [MESSAGE, CALLBACK], ids=["message", "callback_query"])
    def test_two_real_ids_become_two_people(self, template, tenant) -> None:
        people = {
            _person_of(_with(template, 5001, 1001), tenant),
            _person_of(_with(template, 5002, 1002), tenant),
        }

        assert len(people) == 2 and None not in people


class TestWhatIsAKey:
    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", True, False, 1.5, {}, []],
        ids=["null", "empty", "blank", "true", "false", "float", "object", "array"],
    )
    @pytest.mark.parametrize("template", [MESSAGE, CALLBACK], ids=["message", "callback_query"])
    def test_refused(self, value, template) -> None:
        assert parse_inbound(_with(template, value, 1001)) is None

    @pytest.mark.parametrize(
        ("value", "key"),
        [(12345, "12345"), ("12345", "12345"), (0, "0"), (-7, "-7"), (" 12345 ", " 12345 ")],
        # edge spaces are kept: stripping would re-key a person who has them today
        ids=["int", "numeric-string", "zero", "negative", "edge-spaces-kept"],
    )
    def test_kept_as_is(self, value, key) -> None:
        event = parse_inbound(_with(MESSAGE, value, 1001))
        assert event is not None and event.channel_user_id == key
