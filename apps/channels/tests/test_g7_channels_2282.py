"""DRF-2282 — the G7 question ([OD-BOT §170]) on the channel adapters.

* global MAX: the ambiguous message gets the exact question with THREE
  buttons, before the model; a tap is routed by the same skill; the history
  keeps the answer's label, never the raw ``cb:s1g7:`` payload;
* a live «Да, есть хотя бы один признак» tap is «неотложка»: it reaches a
  person under a platform block (DRF-2276) or an operator handoff (N-1, CD
  §67), and the durable G7 STOP is recorded; the other answers keep the mute;
* per-tenant MAX and Telegram: the keyboard is drawn, the tap reaches through
  the block.

MAX sends go through the real ``send_message`` (only the network is faked), so
the block fence is part of the check — the same harness as
``test_platform_block_2276.py``.

Technical checks only. Implementation of the registered G7 owner contract does
not constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.contrib.auth import get_user_model

from apps.channels.max import handler as max_handler
from apps.channels.telegram import handler as tg_handler
from apps.channels.telegram import outbound as tg_outbound
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.identity.services.blocking import BLOCK_NOTICE_TEXT, block_user
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.orchestrator.safety.s1_restriction import restriction
from apps.skills.health_screening.g7_question import (
    ANSWER_LABELS,
    G7_QUESTION_TEXT,
    OUTSIDE_S1_G7_ACK,
    g7_callback,
    g7_pending,
)
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

AMBIGUOUS = "Мне резко стало очень плохо"
CONCIERGE_TEXT = "Какая услуга интересует?"


@pytest.fixture(autouse=True)
def _harness(monkeypatch):
    import apps.channels.max.outbound as outbound
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)
    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def wire(monkeypatch, settings) -> list[dict[str, Any]]:
    """MAX network, faked UNDER the block fence — ``send_message`` is real."""
    import apps.channels.max.outbound as outbound

    settings.MAX_BOT_TOKEN = "test-token-2282"  # pragma: allowlist secret
    settings.MAX_API_BASE = "https://botapi.max.ru"
    sent: list[dict[str, Any]] = []

    def fake_post(url, *, headers=None, params=None, json=None, timeout=None):  # noqa: ANN001, ANN202
        if str(url).endswith("/messages"):
            sent.append(dict(json or {}))
        return httpx.Response(200, json={"message": {}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(outbound.httpx, "post", fake_post)
    return sent


@pytest.fixture
def spy_concierge(monkeypatch) -> MagicMock:
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text=CONCIERGE_TEXT))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def actor(db):  # noqa: ANN001, ANN201
    return get_user_model().objects.create_user("i.operator-2282")


def _msg(text: str, *, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": user_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _tap(payload: str, *, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "timestamp": 1731320000500,
            "callback_id": f"cb-{uuid.uuid4()}",
            "payload": payload,
            "user": {"user_id": user_id, "name": "Иван", "lang": "ru"},
        },
        "message": {
            "recipient": {"chat_id": user_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": G7_QUESTION_TEXT, "attachments": []},
        },
    }


def _global(update: dict) -> None:
    max_handler.handle_global_max_event(update, trace_id=str(uuid.uuid4()))


def _global_conversation(user_id: int) -> Conversation:
    bot_user = BotUser.all_tenants.get(channel="max", channel_user_id=str(user_id))
    return Conversation.all_tenants.filter(bot_user=bot_user, is_active=True).latest("id")


def _token(conversation: Conversation) -> str:
    pending = g7_pending(Conversation.all_tenants.get(pk=conversation.pk))
    assert pending is not None
    return pending.token


def _button_payloads(sent: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for attachment in sent.get("attachments") or []:
        for row in (attachment.get("payload") or {}).get("buttons") or []:
            for button in row if isinstance(row, list) else [row]:
                out.append(button.get("payload", ""))
    return out


# ── global MAX ───────────────────────────────────────────────────────


class TestGlobalMax:
    def test_the_question_with_three_buttons_before_the_model(self, wire, spy_concierge) -> None:
        _global(_msg(AMBIGUOUS, user_id=8201, mid="a"))

        assert wire[-1]["text"] == G7_QUESTION_TEXT
        payloads = _button_payloads(wire[-1])
        assert len(payloads) == 3 and all(p.startswith("cb:s1g7:") for p in payloads)
        spy_concierge.assert_not_called()

    def test_a_no_tap_is_routed_and_history_keeps_the_label(self, wire, spy_concierge) -> None:
        _global(_msg(AMBIGUOUS, user_id=8202, mid="a"))
        conversation = _global_conversation(8202)
        tap = g7_callback("no", _token(conversation))

        _global(_tap(tap, user_id=8202, mid="b"))

        assert wire[-1]["text"] == OUTSIDE_S1_G7_ACK
        spy_concierge.assert_not_called()
        contents = list(
            Message.all_tenants.filter(conversation=conversation, role="user").values_list(
                "content", flat=True
            )
        )
        assert ANSWER_LABELS["no"] in contents  # the label, as what the person said
        assert not any(c.startswith("cb:s1g7:") for c in contents)  # never the payload

    def test_a_yes_tap_is_the_durable_stop(self, wire, spy_concierge) -> None:
        _global(_msg(AMBIGUOUS, user_id=8203, mid="a"))
        conversation = _global_conversation(8203)

        _global(_tap(g7_callback("yes", _token(conversation)), user_id=8203, mid="b"))

        assert wire[-1]["text"] == MEDICAL_EMERGENCY_TEXT_V2
        rec = restriction(BotUser.all_tenants.get(pk=conversation.bot_user_id))
        assert rec is not None and rec.status == "stop" and rec.group == "G7"

    def test_a_yes_tap_reaches_a_blocked_person(self, wire, spy_concierge, actor) -> None:
        _global(_msg(AMBIGUOUS, user_id=8204, mid="a"))
        conversation = _global_conversation(8204)
        token = _token(conversation)
        block_user(
            actor=actor,
            bot_user=BotUser.all_tenants.get(pk=conversation.bot_user_id),
            reason="шлёт рекламный спам в чат",
        )

        _global(_tap(g7_callback("yes", token), user_id=8204, mid="b"))

        assert wire[-1]["text"] == MEDICAL_EMERGENCY_TEXT_V2
        assert BLOCK_NOTICE_TEXT not in [s["text"] for s in wire]
        rec = restriction(BotUser.all_tenants.get(pk=conversation.bot_user_id))
        assert rec is not None and rec.status == "stop" and rec.group == "G7"

    def test_a_no_tap_of_a_blocked_person_gets_the_notice(self, wire, spy_concierge, actor) -> None:
        _global(_msg(AMBIGUOUS, user_id=8205, mid="a"))
        conversation = _global_conversation(8205)
        token = _token(conversation)
        block_user(
            actor=actor,
            bot_user=BotUser.all_tenants.get(pk=conversation.bot_user_id),
            reason="шлёт рекламный спам в чат",
        )

        _global(_tap(g7_callback("no", token), user_id=8205, mid="b"))

        assert wire[-1]["text"] == BLOCK_NOTICE_TEXT
        assert restriction(BotUser.all_tenants.get(pk=conversation.bot_user_id)) is None

    def test_a_yes_tap_reaches_through_a_handoff(self, wire, spy_concierge, monkeypatch) -> None:
        _global(_msg(AMBIGUOUS, user_id=8206, mid="a"))
        conversation = _global_conversation(8206)
        token = _token(conversation)
        monkeypatch.setattr(max_handler, "global_handoff_muted", lambda **kw: True)
        monkeypatch.setattr(max_handler, "notify_safety_reply_during_handoff", lambda **kw: None)

        _global(_tap(g7_callback("yes", token), user_id=8206, mid="b"))

        assert wire[-1]["text"] == MEDICAL_EMERGENCY_TEXT_V2


# ── per-tenant MAX ───────────────────────────────────────────────────


class TestPerTenantMax:
    def test_the_question_then_a_yes_tap_through_the_block(self, wire, actor) -> None:
        tenant = Tenant.objects.create(slug="g7-2282-pt", name="T")

        def run(update: dict) -> None:
            trace = uuid.uuid4()
            with tenant_scope(tenant), trace_id_scope(str(trace)):
                max_handler.handle_max_event(update, trace_id=trace)

        run(_msg(AMBIGUOUS, user_id=8211, mid="a"))
        assert wire[-1]["text"] == G7_QUESTION_TEXT
        assert len(_button_payloads(wire[-1])) == 3
        bot_user = BotUser.all_tenants.get(tenant=tenant, channel="max", channel_user_id="8211")
        conversation = Conversation.all_tenants.filter(bot_user=bot_user).latest("id")
        token = _token(conversation)
        block_user(actor=actor, bot_user=bot_user, reason="шлёт рекламный спам в чат")
        wire.clear()

        run(_tap(g7_callback("yes", token), user_id=8211, mid="b"))

        assert [s["text"] for s in wire] == [MEDICAL_EMERGENCY_TEXT_V2]
        rec = restriction(BotUser.all_tenants.get(pk=bot_user.pk))
        assert rec is not None and rec.status == "stop" and rec.group == "G7"


# ── Telegram ─────────────────────────────────────────────────────────


class TestTelegram:
    @staticmethod
    def _tenant(slug: str) -> Tenant:
        return Tenant.objects.create(
            slug=slug,
            name="TG",
            telegram_bot_token=f"bot-token-{slug}",  # pragma: allowlist secret
            telegram_webhook_secret=f"secret-{slug}",  # pragma: allowlist secret
        )

    @staticmethod
    def _message(text: str, n: int) -> dict:
        return {
            "update_id": 9100 + n,
            "message": {
                "message_id": n,
                "date": 1731320000,
                "from": {"id": 8221, "is_bot": False, "first_name": "Иван"},
                "chat": {"id": 8221, "type": "private"},
                "text": text,
            },
        }

    @staticmethod
    def _callback(data: str, n: int) -> dict:
        return {
            "update_id": 9100 + n,
            "callback_query": {
                "id": f"cq-{n}",
                "from": {"id": 8221, "is_bot": False, "first_name": "Иван"},
                "message": {
                    "message_id": n,
                    "date": 1731320000,
                    "chat": {"id": 8221, "type": "private"},
                    "text": G7_QUESTION_TEXT,
                },
                "data": data,
            },
        }

    def test_keyboard_then_a_yes_tap_through_the_block(self, actor) -> None:
        tenant = self._tenant("g7-2282-tg")
        with patch.object(
            tg_outbound.requests,
            "post",
            return_value=SimpleNamespace(ok=True, status_code=200, text='{"ok":true}'),
        ) as post:
            with tenant_scope(tenant):
                tg_handler.handle_inbound(self._message(AMBIGUOUS, 1), tenant=tenant)
            sends = [c.kwargs["json"] for c in post.call_args_list if "sendMessage" in c.args[0]]
            assert sends[-1]["text"] == G7_QUESTION_TEXT
            rows = sends[-1]["reply_markup"]["inline_keyboard"]
            assert [row[0]["text"] for row in rows] == [
                ANSWER_LABELS[a] for a in ("yes", "no", "unsure")
            ]
            assert all(row[0]["callback_data"].startswith("cb:s1g7:") for row in rows)

            bot_user = BotUser.all_tenants.get(tenant=tenant, channel="telegram")
            conversation = Conversation.all_tenants.filter(bot_user=bot_user).latest("id")
            token = _token(conversation)
            block_user(actor=actor, bot_user=bot_user, reason="шлёт рекламный спам в чат")
            post.reset_mock()
            with tenant_scope(tenant):
                tg_handler.handle_inbound(
                    self._callback(g7_callback("yes", token), 2), tenant=tenant
                )
            texts = [
                c.kwargs["json"]["text"] for c in post.call_args_list if "sendMessage" in c.args[0]
            ]
        assert texts == [MEDICAL_EMERGENCY_TEXT_V2]
        rec = restriction(BotUser.all_tenants.get(pk=bot_user.pk))
        assert rec is not None and rec.status == "stop" and rec.group == "G7"
