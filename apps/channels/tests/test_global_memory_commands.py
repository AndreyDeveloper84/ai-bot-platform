"""Global-path chat memory commands, end-to-end through the handler (M-B4 / #1113).

Reuses the tenant-less global MAX harness. Verifies the M-B4 commands intercept
only when memory is ACTIVE (ayla_user_id + PERSONAL_DATA consent), stay dormant
otherwise (discovery unchanged), and that «забудь всё» is a two-step in-chat
confirmation.
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.consent.services import record_global_consent
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db(transaction=True)


def _payload(*, text: str, mid: str, user_id: int, chat_id: int) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _entry(payload: dict) -> dict:
    return {"data": json.dumps(payload), "trace_id": str(uuid.uuid4()), "resolved_tenant_id": ""}


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        max_handler,
        "send_message",
        lambda *, chat_id, text, attachments=None, timeout=10.0: (
            calls.append({"text": text}) or {"ok": True}
        ),
    )
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()  # ONE instance so short-term persists across turns
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)


@pytest.fixture
def mock_discovery(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    monkeypatch.setattr(
        "apps.orchestrator.concierge.generate_concierge_reply",
        lambda *a, **k: DiscoveryReply(text="__DISCOVERY__"),
    )


def _memory_user(user_id: int, ayla_uid: uuid.UUID, *, consent: bool, green: bool = True):
    bu = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), ayla_user_id=ayla_uid
    )
    if consent:
        record_global_consent(bu, source="welcome")
    upc = UserPersonalContext.objects.create(user_id=ayla_uid)
    if green:
        MemoryEntry.objects.create(
            user_id=ayla_uid,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            provenance=MemoryEntry.PROVENANCE_USER_STATED,  # CHECK 5 (DRF-1263)
            kind="lifestyle",
            content={"key": "diet", "value": "vegan"},
        )
    return bu


class TestShowCommand:
    def test_intercepts_when_memory_active(self, settings, mock_send, fake_redis, mock_discovery):
        settings.STRICT_TENANT_SCOPE = "strict"
        _memory_user(7001, uuid.uuid4(), consent=True)
        GlobalMaxHandler()(
            _entry(_payload(text="покажи что знаешь обо мне", mid="s1", user_id=7001, chat_id=8001))
        )
        assert len(mock_send) == 1
        assert "веганского питания" in mock_send[0]["text"]

    def test_dormant_without_consent_falls_through_to_discovery(
        self, settings, mock_send, fake_redis, mock_discovery
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        _memory_user(7002, uuid.uuid4(), consent=False)
        GlobalMaxHandler()(
            _entry(_payload(text="покажи что знаешь обо мне", mid="s2", user_id=7002, chat_id=8002))
        )
        assert len(mock_send) == 1
        assert mock_send[0]["text"] == "__DISCOVERY__"  # command not intercepted


class TestForgetFieldDoesNotResurrect:
    def test_forget_command_does_not_re_learn_the_fact(
        self, settings, mock_send, fake_redis, mock_discovery
    ):
        # «забудь что я веган» contains the substring «я веган»; the end-of-turn
        # fact learner MUST be skipped on a memory-command turn, or the fact the
        # user just erased is instantly re-created (152-ФЗ erasure nullified).
        settings.STRICT_TENANT_SCOPE = "strict"
        ayla_uid = uuid.uuid4()
        _memory_user(7004, ayla_uid, consent=True)

        GlobalMaxHandler()(
            _entry(_payload(text="забудь что я веган", mid="ff1", user_id=7004, chat_id=8004))
        )
        assert "забыла" in mock_send[-1]["text"].lower()
        # No LIVE green entry remains — the fact stays erased.
        live = MemoryEntry.objects.filter(
            user_id=ayla_uid,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            soft_deleted_at__isnull=True,
        )
        assert live.count() == 0


class TestForgetAllTwoStep:
    def test_two_step_confirmation_sets_forget_all(
        self, settings, mock_send, fake_redis, mock_discovery
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        ayla_uid = uuid.uuid4()
        _memory_user(7003, ayla_uid, consent=True)

        # Turn 1: «забудь всё» → confirmation prompt, nothing deleted yet.
        GlobalMaxHandler()(
            _entry(_payload(text="забудь всё", mid="f1", user_id=7003, chat_id=8003))
        )
        assert "удалить" in mock_send[-1]["text"].lower()
        assert UserPersonalContext.objects.get(user_id=ayla_uid).forget_all_requested_at is None

        # Turn 2: «удалить» → executes (forget_all_requested_at set).
        GlobalMaxHandler()(_entry(_payload(text="удалить", mid="f2", user_id=7003, chat_id=8003)))
        assert UserPersonalContext.objects.get(user_id=ayla_uid).forget_all_requested_at is not None
