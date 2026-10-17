"""DRF-2725 — канальная проверка ответа получает ту же лицензию знания, что и консьерж.

Вторая половина пути лицензии (первая —
``apps/orchestrator/tests/test_knowledge_licence_travels_2725.py``). Канал
проверяет ответ консьержа второй раз, перед отправкой. Знания он не читал, и
без лицензии судил бы вслепую — поэтому она едет в результате хода через шов
(``DiscoveryReply`` → ``TurnReply``) рядом с трассой инструментов.

Узлы идут сквозь НАСТОЯЩИЙ шов и НАСТОЯЩИЙ обработчик канала: подменён только
мозг (что сказал консьерж) и наблюдается вызов хука. До хука должен дойти тот
же объект, который вернул консьерж, — проверяется тождество, а не равенство.

Читателя знания в этом листе нет. На живом пути каждый ход сегодня несёт
``None``; последний узел держит это прямо.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.knowledge_licence import (
    KnowledgeLicence,
    LicensedClaim,
    LicensedSubject,
    SubjectState,
)
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety import gate as gate_mod

pytestmark = pytest.mark.django_db

_CLEAN = "Массаж спины у Дениса стоит 3 500 ₽ за час. Записать на завтра в 18:00?"

LICENCE = KnowledgeLicence(
    read_at=datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC),
    subjects=(
        LicensedSubject(
            template_id="tpl-massage",
            state=SubjectState.KNOWN,
            claims=(LicensedClaim(claim_id="claim-1", kind="capability", key="relaxation"),),
        ),
    ),
)


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_chat_action(monkeypatch):
    import apps.channels.max.outbound as outbound

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)


@pytest.fixture(autouse=True)
def _strict(settings):
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.STRICT_TENANT_REFUSE = True


def _run_global(text: str, *, mid: str, user_id: int) -> None:
    max_handler.handle_global_max_event(
        {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Иван"},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
            },
        },
        trace_id=str(uuid.uuid4()),
    )


def _concierge_says(monkeypatch, reply: DiscoveryReply) -> None:
    """Пропатчено там, где символ импортирует шов, — шов и обработчик идут настоящие."""
    monkeypatch.setattr(
        "apps.orchestrator.concierge.generate_concierge_reply", MagicMock(return_value=reply)
    )


def _spy_on_channel_guard(monkeypatch) -> list[dict]:
    """Записывать аргументы хука в обработчике канала, не подменяя его работу."""
    real = gate_mod.guard_outbound
    seen: list[dict] = []

    def spy(text, **kwargs):
        seen.append({"text": text, **kwargs})
        return real(text, **kwargs)

    monkeypatch.setattr(max_handler, "guard_outbound", spy)
    return seen


def _calls_on_the_reply(seen: list[dict], text: str) -> list[dict]:
    """Вызовы хука на САМ ответ: у обработчика есть и другие (служебная строка и пр.)."""
    return [call for call in seen if call["text"] == text]


class TestTheChannelGuardSeesTheSameLicence:
    def test_the_licence_crosses_the_seam_to_the_channel_guard(
        self, mock_send, fake_redis, monkeypatch
    ):
        _concierge_says(monkeypatch, DiscoveryReply(text=_CLEAN, knowledge_licence=LICENCE))
        seen = _spy_on_channel_guard(monkeypatch)

        _run_global("что даёт массаж спины", mid="lic-chan-1", user_id=7251)

        (call,) = _calls_on_the_reply(seen, _CLEAN)
        assert call["surface"] == "max"
        assert call["knowledge"] is LICENCE
        assert mock_send[0]["text"] == _CLEAN

    def test_today_every_turn_carries_none(self, mock_send, fake_redis, monkeypatch):
        """Читателя знания ещё нет: консьерж лицензии не создаёт, канал получает ``None``."""
        _concierge_says(monkeypatch, DiscoveryReply(text=_CLEAN))
        seen = _spy_on_channel_guard(monkeypatch)

        _run_global("сколько стоит массаж", mid="lic-chan-2", user_id=7252)

        (call,) = _calls_on_the_reply(seen, _CLEAN)
        assert call["knowledge"] is None
        assert mock_send[0]["text"] == _CLEAN
