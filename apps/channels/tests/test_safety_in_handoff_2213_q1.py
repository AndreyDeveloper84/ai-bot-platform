"""DRF-2213 Q1 — кризис и неотложка получают ответ и во время handoff.

Решение владельца N-1 (CD §67, AYLA-DEC-0096), дословно из брифа: «кризис и
неотложка получают детерминированный ответ всегда, в том числе при согласии,
не данном или отозванном, и при работе оператора». Оно перекрывает замысел
S1-B (#1053) / REPLY_DRF-1015 №1 «не перебивать оператора» — для этих двух
вердиктов гейта, и только для них.

Замер (ветка Q2, 21.09) — глушение на трёх путях:

* global MAX — ``global_handoff_muted`` делает ``return`` ДО
  ``evaluate_inbound``: кризисная / медицинская реплика в handoff не получает
  ничего;
* per-tenant MAX и Telegram — гейт считает вердикт, но
  ``conversation.state != HUMAN_HANDOFF`` глушит ответ.

Положительные пары: обычная реплика в handoff молчит; ``BLOCK`` (лекарства,
юрвопрос) в handoff молчит — N-1 называет только кризис и неотложку.
"""

from __future__ import annotations

import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.telegram import handler as tg_handler
from apps.channels.telegram import outbound as tg_outbound
from apps.conversations.models import Conversation
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import CRISIS_HOTLINE, CRISIS_REPLY_TEXT
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SAFETY_UNDER_HANDOFF = (
    ("я думаю о суициде", CRISIS_REPLY_TEXT),
    ("у меня сердечный приступ", MEDICAL_EMERGENCY_TEXT_V2),
)


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture(autouse=True)
def _harness(monkeypatch):
    import apps.channels.max.outbound as outbound
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)
    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def spy_concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


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


def _run_global(text: str, *, user_id: int, mid: str) -> None:
    max_handler.handle_global_max_event(
        _msg(text, user_id=user_id, mid=mid), trace_id=str(uuid.uuid4())
    )


def _global_conversation(user_id: int) -> Conversation:
    bot_user = BotUser.all_tenants.get(
        channel="max", channel_user_id=str(user_id), tenant__slug=GLOBAL_BOT_TENANT_SLUG
    )
    return Conversation.all_tenants.get(bot_user=bot_user, tenant__slug=GLOBAL_BOT_TENANT_SLUG)


def _kind(expected: str) -> str:
    """Детерминированная метка параметра для slug тенанта (не ``hash()``:
    он солится по процессу — DRF-1158)."""
    return "crisis" if expected == CRISIS_REPLY_TEXT else "medical"


# ── p1 — handoff не глушит кризис и неотложку ────────────────────────


class TestP1SafetyReachesThePersonDuringHandoff:
    @pytest.mark.parametrize(("text", "expected"), SAFETY_UNDER_HANDOFF)
    def test_global_path(self, sent, spy_concierge, text, expected) -> None:
        _run_global("оператор", user_id=4401, mid="a")
        assert len(sent) == 1  # положительно: handoff открыт, подтверждение ушло
        _run_global("вы тут?", user_id=4401, mid="b")  # эпизодное уведомление о молчании
        before = len(sent)

        _run_global(text, user_id=4401, mid="c")

        assert [call["text"] for call in sent[before:]] == [expected]
        spy_concierge.assert_not_called()  # и модель по-прежнему не зовётся

    @pytest.mark.parametrize(("text", "expected"), SAFETY_UNDER_HANDOFF)
    def test_per_tenant_max(self, sent, text, expected) -> None:
        tenant = Tenant.objects.create(slug=f"drf2213-{_kind(expected)}", name="T")

        def run(t: str, mid: str) -> None:
            trace = uuid.uuid4()
            with tenant_scope(tenant), trace_id_scope(str(trace)):
                max_handler.handle_max_event(_msg(t, user_id=4402, mid=mid), trace_id=trace)

        run("привет", "a")
        conv = Conversation.all_tenants.get(tenant=tenant)
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
        sent.clear()

        run(text, "b")

        assert [call["text"] for call in sent] == [expected]

    @pytest.mark.parametrize(("text", "expected"), SAFETY_UNDER_HANDOFF)
    def test_telegram(self, text, expected) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        tenant = Tenant.objects.create(
            slug=f"drf2213-tg-{_kind(expected)}",
            name="TG",
            telegram_bot_token="bot-token-2213",  # pragma: allowlist secret
            telegram_webhook_secret="secret-2213",  # pragma: allowlist secret
        )

        def payload(t: str, message_id: int) -> dict:
            return {
                "update_id": 7000 + message_id,
                "message": {
                    "message_id": message_id,
                    "date": 1731320000,
                    "from": {"id": 4403, "is_bot": False, "first_name": "Иван"},
                    "chat": {"id": 4403, "type": "private"},
                    "text": t,
                },
            }

        with patch.object(
            tg_outbound.requests,
            "post",
            return_value=SimpleNamespace(ok=True, status_code=200, text='{"ok":true}'),
        ) as post:
            with tenant_scope(tenant):
                tg_handler.handle_inbound(payload("привет", 1), tenant=tenant)
            conv = Conversation.all_tenants.get(tenant=tenant)
            Conversation.all_tenants.filter(pk=conv.pk).update(
                state=Conversation.State.HUMAN_HANDOFF
            )
            post.reset_mock()
            with tenant_scope(tenant):
                tg_handler.handle_inbound(payload(text, 2), tenant=tenant)
            texts = [
                c.kwargs["json"]["text"] for c in post.call_args_list if "sendMessage" in c.args[0]
            ]

        assert texts == [expected]

    def test_plain_turn_during_handoff_stays_silent(self, sent, spy_concierge) -> None:
        """Положительная пара: обычная реплика в handoff по-прежнему молчит —
        правка не должна превратить handoff в «бот говорит поверх оператора»."""
        _run_global("оператор", user_id=4404, mid="a")
        _run_global("вы тут?", user_id=4404, mid="b")
        before = len(sent)
        _run_global("когда ответите?", user_id=4404, mid="c")
        assert len(sent) == before  # empty-assert-ok: handoff открыт — проверено в p1
        assert CRISIS_HOTLINE not in " ".join(call["text"] for call in sent)

    def test_block_during_handoff_stays_silent_global(self, sent, spy_concierge) -> None:
        """N-1 называет кризис и неотложку. BLOCK (лекарство, юрвопрос) — не
        из них: в handoff бот молчит, как решено в S1-B."""
        _run_global("оператор", user_id=4410, mid="a")
        _run_global("вы тут?", user_id=4410, mid="b")
        before = len(sent)
        _run_global("посоветуйте ибупрофен от боли", user_id=4410, mid="c")
        assert len(sent) == before  # empty-assert-ok: handoff открыт — «оператор» выше

    def test_block_during_handoff_stays_silent_per_tenant(self, sent) -> None:
        tenant = Tenant.objects.create(slug="drf2213-block", name="T")

        def run(t: str, mid: str) -> None:
            trace = uuid.uuid4()
            with tenant_scope(tenant), trace_id_scope(str(trace)):
                max_handler.handle_max_event(_msg(t, user_id=4411, mid=mid), trace_id=trace)

        run("привет", "a")
        assert sent  # положительно: до handoff бот отвечает
        conv = Conversation.all_tenants.get(tenant=tenant)
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
        sent.clear()
        run("как подать в суд на салон", "b")
        assert sent == []  # empty-assert-ok: положительный ответ до handoff проверен выше
