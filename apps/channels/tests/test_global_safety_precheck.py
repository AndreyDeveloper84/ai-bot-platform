"""Global (tenant-less) MAX safety pre-check integration (#1053, S1-B).

Proves the red-flag / BLOCK short-circuit fires on the LIVE global path
(`_handle_global_max_event_inner`) BEFORE onboarding / discovery, that the
tenant-less path creates NO AdminTask (Variant A, founder 2026-07-03 — #1076),
keeps `current_tenant() is None`, and that a normal message still reaches
discovery.
"""

from __future__ import annotations

import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.handoff.models import AdminTask
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import BLOCK_REPLY_TEXT, CRISIS_REPLY_TEXT
from apps.tenancy.context import current_tenant

pytestmark = pytest.mark.django_db


def _payload(*, text, user_id=7777, chat_id=8888, mid="m-1"):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def spy_discovery(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def spy_direct_show_masters(monkeypatch):
    # DRF-1102 — a general booking/service phrase now short-circuits to this
    # deterministic function instead of the concierge LLM.
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Вот мастера, которые могут подойти:"))
    monkeypatch.setattr(max_handler, "generate_direct_show_masters_reply", spy)
    return spy


@pytest.fixture(autouse=True)
def _strict(settings):
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.STRICT_TENANT_REFUSE = True


class TestGlobalSafety:
    def test_suicide_returns_crisis_skips_discovery_no_admin_task(
        self, mock_send, fake_redis, spy_discovery, spy_direct_show_masters
    ):
        max_handler.handle_global_max_event(
            _payload(text="я думаю о суициде"), trace_id=str(uuid.uuid4())
        )

        assert len(mock_send) == 1
        assert mock_send[0]["text"] == CRISIS_REPLY_TEXT
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_not_called()
        # Variant A: tenant-less path never creates an AdminTask.
        assert AdminTask.all_tenants.count() == 0
        assert current_tenant() is None

    def test_block_phrase_returns_block_reply(
        self, mock_send, fake_redis, spy_discovery, spy_direct_show_masters
    ):
        max_handler.handle_global_max_event(
            _payload(text="как подать в суд на мастера"), trace_id=str(uuid.uuid4())
        )
        assert mock_send[0]["text"] == BLOCK_REPLY_TEXT
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_not_called()
        assert AdminTask.all_tenants.count() == 0

    def test_expanded_phrase_reaches_crisis_end_to_end(
        self, mock_send, fake_redis, spy_discovery, spy_direct_show_masters
    ):
        # #1081: a newly-covered phrasing («покончу с собой») → crisis reply on
        # the live global path, not discovery.
        max_handler.handle_global_max_event(
            _payload(text="я хочу покончить с собой"), trace_id=str(uuid.uuid4())
        )
        assert mock_send[0]["text"] == CRISIS_REPLY_TEXT
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_not_called()
        assert AdminTask.all_tenants.count() == 0

    def test_normal_message_reaches_discovery(
        self, mock_send, fake_redis, spy_discovery, spy_direct_show_masters
    ):
        # Onboarding flag OFF (default) → normal discovery path, unaffected by the gate.
        # DRF-1102: «хочу маникюр завтра» names a service, so it now short-circuits
        # to the deterministic show-masters branch instead of the concierge LLM.
        max_handler.handle_global_max_event(
            _payload(text="хочу маникюр завтра"), trace_id=str(uuid.uuid4())
        )
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_called_once()
        assert mock_send[0]["text"] not in (CRISIS_REPLY_TEXT, BLOCK_REPLY_TEXT)
