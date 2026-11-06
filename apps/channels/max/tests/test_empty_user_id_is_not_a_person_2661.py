"""DRF-2661: an empty MAX user id is not a person.

``"user_id" in d`` holds for ``{"user_id": null}``, and ``str(None)`` is
``"None"``: every event with an empty id would resolve to ONE person — two
strangers sharing a shell, a memory and a history. Reachability today is nil
(every site sits behind the MAX sender's signature); the guard lives here,
in the layer that turns a payload into a key.

The node is the merge, not the parser's return value: two events with an
empty id, from two different chats, must not become one person. The pair —
two real ids — must become two.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from apps.channels.max.parser import ParseError, parse_max_webhook
from apps.identity.models import BotUser
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.miniapp_api.auth import InitDataMalformed, verify_init_data

MESSAGE = {
    "update_type": "message_created",
    "timestamp": 1731320000000,
    "message": {
        "sender": {"user_id": 12345, "name": "Иван"},
        "recipient": {"chat_id": 67890, "chat_type": "dialog"},
        "body": {"mid": "m-1", "seq": 1, "text": "Привет", "attachments": []},
    },
}
CALLBACK = {
    "update_type": "message_callback",
    "timestamp": 1731320000000,
    "callback": {
        "timestamp": 1731320000500,
        "callback_id": "cb-1",
        "payload": "cb:welcome:book",
        "user": {"user_id": 12345, "name": "Иван"},
    },
    "message": {"recipient": {"chat_id": 67890, "chat_type": "dialog"}, "body": {"mid": "m-1"}},
}
STARTED = {
    "update_type": "bot_started",
    "timestamp": 1731320000000,
    "chat_id": 67890,
    "user": {"user_id": 12345, "name": "Иван"},
}


def _with(template: dict, user_id, chat_id: int) -> dict:
    payload = copy.deepcopy(template)
    if payload["update_type"] == "message_created":
        payload["message"]["sender"]["user_id"] = user_id
        payload["message"]["recipient"]["chat_id"] = chat_id
    elif payload["update_type"] == "message_callback":
        payload["callback"]["user"]["user_id"] = user_id
        payload["message"]["recipient"]["chat_id"] = chat_id
    else:
        payload["user"]["user_id"] = user_id
        payload["chat_id"] = chat_id
    return payload


def _person_of(payload: dict) -> str | None:
    """What the handler does: parse, then resolve the person by the key."""

    try:
        event = parse_max_webhook(payload)
    except ParseError:
        return None
    return str(
        resolve_or_create_global_bot_user(
            channel=event.channel, channel_user_id=event.channel_user_id, chat_id=event.chat_id
        ).pk
    )


@pytest.mark.django_db
class TestTwoEmptyIdsAreNotOnePerson:
    @pytest.mark.parametrize(
        "template", [MESSAGE, CALLBACK, STARTED], ids=lambda t: t["update_type"]
    )
    def test_two_empty_ids_from_two_chats_become_nobody(self, template) -> None:
        people = {_person_of(_with(template, None, 1001)), _person_of(_with(template, None, 1002))}

        assert people == {None}
        assert not BotUser.all_tenants.filter(channel_user_id="None").exists()

    @pytest.mark.parametrize(
        "template", [MESSAGE, CALLBACK, STARTED], ids=lambda t: t["update_type"]
    )
    def test_two_real_ids_become_two_people(self, template) -> None:
        people = {_person_of(_with(template, 5001, 1001)), _person_of(_with(template, 5002, 1002))}

        assert len(people) == 2 and None not in people


class TestWhatIsAKey:
    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", True, False, 1.5, {}, []],
        ids=["null", "empty", "blank", "true", "false", "float", "object", "array"],
    )
    def test_refused(self, value) -> None:
        with pytest.raises(ParseError, match="not a usable id"):
            parse_max_webhook(_with(MESSAGE, value, 1001))

    @pytest.mark.parametrize(
        ("value", "key"),
        [(12345, "12345"), ("12345", "12345"), (0, "0"), (-7, "-7"), (" 12345 ", " 12345 ")],
        # edge spaces are kept: stripping would re-key a person who has them today
        ids=["int", "numeric-string", "zero", "negative", "edge-spaces-kept"],
    )
    def test_kept_as_is(self, value, key) -> None:
        assert parse_max_webhook(_with(MESSAGE, value, 1001)).channel_user_id == key


BOT_TOKEN = "test-bot-token-2661"


def _signed_launch(user: dict) -> str:
    params = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest})


class TestMiniAppLaunch:
    """The same key from the other door: ``VerifiedInitData.user_id``."""

    @pytest.mark.parametrize("uid", [None, "", "  ", True], ids=["null", "empty", "blank", "true"])
    def test_an_empty_id_is_refused(self, uid) -> None:
        with pytest.raises(InitDataMalformed, match="not a usable id"):
            verify_init_data(_signed_launch({"id": uid}), bot_token=BOT_TOKEN)

    def test_a_real_id_is_the_key(self) -> None:
        verified = verify_init_data(_signed_launch({"id": 5001}), bot_token=BOT_TOKEN)
        assert verified.user_id == "5001"
