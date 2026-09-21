"""DRF-2213 Q1, надстройка — решение владельца «все по рекомендациям» (CD §72).

* **1в** — red flag G1–G7 классификатора (``health_screening.classify`` →
  ``RED_FLAG``, не «неотложка» гейта) — тоже «неотложка» по N-1: отвечать
  детерминированным медицинским текстом и при работе оператора. До надстройки
  глобальный путь глушил его раньше проверки Q2, per-tenant и Telegram — через
  диспетчер навыков при ``HUMAN_HANDOFF``.
* **1а** — когда бот отвечает на кризис или неотложку поверх оператора,
  оператору уходит сигнал без текста клиента: салонный бот (``salon_notify``)
  на per-tenant пути, платформенный канал (``send_max_notification``) на
  глобальном.
* **1б** — новая ``AdminTask`` при открытой не заводится.
"""

from __future__ import annotations

import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.telegram import handler as tg_handler
from apps.channels.telegram import outbound as tg_outbound
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

RED_FLAG = "онемела половина лица"
CRISIS = "я думаю о суициде"


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


@pytest.fixture
def platform_signals(monkeypatch):
    """Платформенный канал операторов — перехват без сети."""
    from apps.handoff import notify

    calls: list[str] = []
    monkeypatch.setattr(notify, "get_notify_addresses", lambda: ("op",))

    def fake(*, text, addresses, on_failure=None):
        calls.append(text)
        return 0

    monkeypatch.setattr(notify, "send_max_notification", fake)
    return calls


@pytest.fixture
def salon_signals(monkeypatch):
    from apps.channels.max import salon_notify

    notices: list = []
    monkeypatch.setattr(salon_notify, "notify", lambda notice: notices.append(notice))
    return notices


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


def _global_handoff(user_id: int, sent) -> int:
    _run_global("оператор", user_id=user_id, mid="a")
    assert len(sent) == 1  # положительно: handoff открыт
    _run_global("вы тут?", user_id=user_id, mid="b")  # эпизодное уведомление о молчании
    return len(sent)


def _per_tenant_handoff(tenant, user_id: int):
    def run(t: str, mid: str) -> None:
        trace = uuid.uuid4()
        with tenant_scope(tenant), trace_id_scope(str(trace)):
            max_handler.handle_max_event(_msg(t, user_id=user_id, mid=mid), trace_id=trace)

    run("привет", "a")
    conv = Conversation.all_tenants.get(tenant=tenant)
    Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
    AdminTask.all_tenants.create(
        tenant=tenant,
        bot_user=conv.bot_user,
        conversation=conv,
        task_type=AdminTask.TaskType.HANDOFF,
        assigned_queue="duty",
    )
    return run


# ── 1в — red flag классификатора отвечается и при handoff ────────────


class TestRedFlagReachesThroughHandoff:
    def test_global(self, sent, spy_concierge, platform_signals) -> None:
        from apps.skills.health_screening.classifier import PainSignal, classify

        assert classify(RED_FLAG) is PainSignal.RED_FLAG  # положительно
        before = _global_handoff(4501, sent)

        _run_global(RED_FLAG, user_id=4501, mid="c")

        assert [c["text"] for c in sent[before:]] == [MEDICAL_EMERGENCY_TEXT_V2]
        spy_concierge.assert_not_called()

    def test_per_tenant(self, sent, salon_signals) -> None:
        tenant = Tenant.objects.create(slug="q1b-pt-rf", name="T")
        run = _per_tenant_handoff(tenant, 4502)
        sent.clear()

        run(RED_FLAG, "b")

        assert [c["text"] for c in sent] == [MEDICAL_EMERGENCY_TEXT_V2]

    def test_telegram(self, salon_signals) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        tenant = Tenant.objects.create(
            slug="q1b-tg-rf",
            name="TG",
            telegram_bot_token="bot-token-q1b",  # pragma: allowlist secret
            telegram_webhook_secret="secret-q1b",  # pragma: allowlist secret
        )

        def payload(t: str, message_id: int) -> dict:
            return {
                "update_id": 8000 + message_id,
                "message": {
                    "message_id": message_id,
                    "date": 1731320000,
                    "from": {"id": 4503, "is_bot": False, "first_name": "Иван"},
                    "chat": {"id": 4503, "type": "private"},
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
                tg_handler.handle_inbound(payload(RED_FLAG, 2), tenant=tenant)
            texts = [
                c.kwargs["json"]["text"] for c in post.call_args_list if "sendMessage" in c.args[0]
            ]

        assert texts == [MEDICAL_EMERGENCY_TEXT_V2]


# ── 1а — сигнал оператору без текста клиента ─────────────────────────


class TestOperatorIsSignalled:
    def test_global_crisis_signals_the_platform_queue(
        self, sent, spy_concierge, platform_signals
    ) -> None:
        _global_handoff(4504, sent)
        platform_signals.clear()

        _run_global(CRISIS, user_id=4504, mid="c")

        assert sent[-1]["text"] == CRISIS_REPLY_TEXT  # положительно: ответ ушёл
        assert len(platform_signals) == 1
        assert "суицид" not in platform_signals[0].lower()  # текст клиента не пересылается
        assert CRISIS not in platform_signals[0]

    def test_per_tenant_red_flag_signals_the_salon(self, sent, salon_signals) -> None:
        tenant = Tenant.objects.create(slug="q1b-pt-sig", name="T")
        run = _per_tenant_handoff(tenant, 4505)

        run(RED_FLAG, "b")

        assert len(salon_signals) == 1
        notice = salon_signals[0]
        rendered = " ".join([notice.title, *notice.facts])
        assert notice.kind == "handoff"
        assert "онемел" not in rendered.lower()  # текст клиента не пересылается

    def test_a_plain_turn_during_handoff_sends_no_signal(
        self, sent, spy_concierge, platform_signals
    ) -> None:
        """Положительная пара: обычная реплика в handoff молчит и сигнала нет."""
        before = _global_handoff(4506, sent)
        platform_signals.clear()

        _run_global("когда ответите?", user_id=4506, mid="c")

        assert len(sent) == before  # empty-assert-ok: handoff открыт — _global_handoff
        assert platform_signals == []  # empty-assert-ok: то же

    def test_no_signal_outside_handoff(self, sent, spy_concierge, platform_signals) -> None:
        """Без handoff кризис получает ответ, а сигнала оператору нет — его некому."""
        _run_global(CRISIS, user_id=4507, mid="a")
        assert sent[-1]["text"] == CRISIS_REPLY_TEXT  # положительно
        assert platform_signals == []  # empty-assert-ok: ответ выше


# ── 1б — новой задачи при открытой не заводится ─────────────────────


class TestNoSecondTask:
    def test_crisis_during_handoff_opens_no_new_task(
        self, sent, spy_concierge, platform_signals
    ) -> None:
        _global_handoff(4508, sent)
        tasks_before = AdminTask.all_tenants.count()
        assert tasks_before == 1  # положительно: задача оператора одна

        _run_global(CRISIS, user_id=4508, mid="c")

        assert AdminTask.all_tenants.count() == tasks_before
