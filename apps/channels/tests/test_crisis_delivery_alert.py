"""Crisis-reply delivery-failure alert (#1082, pre-pilot safety).

A crisis reply that fails to send must raise a high-priority, PII-safe signal
distinct from the ``pre_check_triggered`` event, so ops can follow up out-of-band
(the idempotency key is already claimed → a PEL retry never resends).
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.events.models import Event
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db(transaction=True)

_CRISIS_EVENT = "channels.max.safety.crisis_delivery_failed"


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)


class TestDeliverCrisisReply:
    def _bot_user(self):
        return resolve_or_create_global_bot_user(channel="max", channel_user_id="crisis-1")

    def test_success_sends_and_does_not_alert(self, monkeypatch):
        sent: list[dict] = []
        monkeypatch.setattr(
            max_handler,
            "send_message",
            lambda **kw: sent.append(kw) or {"ok": True},
        )
        bu = self._bot_user()
        max_handler._deliver_crisis_reply(
            chat_id="c1",
            text="держись, вот телефон помощи",
            bot_user=bu,
            trace_id="t1",
            is_global=True,
        )
        assert len(sent) == 1
        assert not Event.objects.filter(event_type=_CRISIS_EVENT).exists()

    def test_failure_alerts_and_reraises(self, monkeypatch):
        def _boom(**kw):
            raise RuntimeError("MAX API down")

        monkeypatch.setattr(max_handler, "send_message", _boom)
        bu = self._bot_user()

        with pytest.raises(RuntimeError):
            max_handler._deliver_crisis_reply(
                chat_id="c1", text="crisis reply text", bot_user=bu, trace_id="t1", is_global=False
            )

        ev = Event.objects.get(event_type=_CRISIS_EVENT)
        assert ev.payload["bot_user_id"] == str(bu.id)
        assert ev.payload["is_global_bot"] is False

    def test_alert_event_and_log_are_pii_safe(self, monkeypatch, caplog):
        import logging

        crisis_text = "я хочу покончить с собой SECRET_CANARY"
        monkeypatch.setattr(
            max_handler, "send_message", lambda **kw: (_ for _ in ()).throw(RuntimeError("down"))
        )
        bu = self._bot_user()
        with caplog.at_level(logging.ERROR, logger="apps.channels.max.handler"):
            with pytest.raises(RuntimeError):
                max_handler._deliver_crisis_reply(
                    chat_id="c1", text=crisis_text, bot_user=bu, trace_id="t1", is_global=True
                )
        ev = Event.objects.get(event_type=_CRISIS_EVENT)
        assert "SECRET_CANARY" not in json.dumps(ev.payload)
        # Neither the event nor the high-priority ERROR log may carry the crisis text.
        assert all("SECRET_CANARY" not in r.getMessage() for r in caplog.records)


class TestRoutingThroughHelper:
    """The crisis turn must route send through the alerting helper; a normal turn
    must not."""

    def _payload(self, text: str, mid: str) -> dict:
        return {
            "data": json.dumps(
                {
                    "update_type": "message_created",
                    "timestamp": 1731320000000,
                    "message": {
                        "sender": {"user_id": 9100, "name": "И"},
                        "recipient": {"chat_id": 9200, "chat_type": "dialog"},
                        "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
                    },
                }
            ),
            "trace_id": str(uuid.uuid4()),
            "resolved_tenant_id": "",
        }

    def test_crisis_turn_uses_alerting_helper(self, fake_redis, monkeypatch):
        # GlobalMaxHandler.requires_tenant=False → runs tenant-less; no strict flag needed.
        calls: list[bool] = []
        monkeypatch.setattr(
            max_handler,
            "_deliver_crisis_reply",
            lambda **kw: calls.append(kw.get("is_global")),
        )
        monkeypatch.setattr(max_handler, "send_message", lambda **kw: {"ok": True})
        # A red-flag phrase trips the safety gate → crisis short-circuit.
        GlobalMaxHandler()(self._payload("хочу покончить с собой", mid="cr1"))
        assert calls == [True]  # routed through the crisis helper on the global path

    def test_normal_turn_uses_plain_send(self, fake_redis, monkeypatch):
        helper_calls: list = []
        sent: list = []
        monkeypatch.setattr(
            max_handler, "_deliver_crisis_reply", lambda **kw: helper_calls.append(kw)
        )
        monkeypatch.setattr(
            max_handler, "send_message", lambda **kw: sent.append(kw) or {"ok": True}
        )
        from apps.orchestrator.discovery import DiscoveryReply

        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda *a, **k: DiscoveryReply(text="привет"),
        )
        GlobalMaxHandler()(self._payload("хочу маникюр", mid="nr1"))
        assert helper_calls == []  # normal turn does NOT use the crisis helper
        assert len(sent) == 1
