"""DRF-2213 (safety P0, §63) — safety не уступает handoff и соседним путям.

Замер на ``fix/drf2000-medical-escalation`` (22a9e029), 21.09:

* p1 — handoff. Global: ``global_handoff_muted`` делает ``return`` ДО
  ``evaluate_inbound`` → кризисная / медицинская реплика, пока диалог у
  оператора, не получает НИЧЕГО (одно уведомление о молчании за эпизод —
  и то только первым входящим). Per-tenant MAX и Telegram: гейт считает
  вердикт, но явно пропускает ответ при ``HUMAN_HANDOFF`` («не перебивать
  оператора», S1-B #1053) — тоже тишина. Требование главного окна (DRF-2213):
  кризис / неотложка → детерминированный текст ДАЖЕ в handoff.
* p2 — соседние пути red flag G1–G7 (классификатор, не группа гейта) на
  global: онбординг нового человека стоит ПЕРЕД консьержем → «онемела
  половина лица» первым сообщением получает приветствие, а не 103 / 112.
* p3 — проактив (nutrition report / coach / water) не читает safety: общий
  гейт ``selection.check_common`` смотрит согласия, отписку, периметр, но
  не то, что сегодня человек получил кризисный / медицинский ответ.

Все тесты этого файла КРАСНЫЕ на замере — это и есть замер.
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
        tenant = Tenant.objects.create(slug=f"drf2213-{abs(hash(text)) % 10_000}", name="T")

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
            slug=f"drf2213-tg-{abs(hash(text)) % 10_000}",
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


# ── p2 — соседние пути red flag на global ────────────────────────────


class TestP2RedFlagBeatsOnboarding:
    def test_first_message_red_flag_gets_the_emergency_text(
        self, sent, spy_concierge, settings
    ) -> None:
        from apps.skills.health_screening.classifier import PainSignal, classify

        settings.GLOBAL_BOT_ONBOARDING = True
        text = "онемела половина лица"
        assert classify(text) == PainSignal.RED_FLAG  # положительно: red flag классификатора

        _run_global(text, user_id=4405, mid="a")

        assert sent, "ничего не отправлено"
        assert sent[0]["text"] == MEDICAL_EMERGENCY_TEXT_V2


# ── p3 — проактив читает safety ──────────────────────────────────────


class TestP3ProactiveReadsSafety:
    @pytest.mark.parametrize("text", ["я думаю о суициде", "у меня сердечный приступ"])
    def test_common_gate_blocks_after_a_safety_turn(self, sent, spy_concierge, text) -> None:
        from apps.nutrition_proactive import selection

        _run_global("привет", user_id=4406, mid="a")
        _run_global(text, user_id=4406, mid="b")
        assert sent[-1]["text"] in (CRISIS_REPLY_TEXT, MEDICAL_EMERGENCY_TEXT_V2)  # положительно
        from django.utils import timezone

        from apps.consent.nutrition import FOOD_DIARY_CONSENT_DOCUMENT_VERSION, grant_diary
        from apps.consent.services import record_global_consent

        bot_user = _global_conversation(4406).bot_user
        bot_user.consent_at = timezone.now()
        bot_user.save(update_fields=["consent_at"])
        record_global_consent(bot_user, source="test:drf2213", document_version="welcome-s2-v1")
        grant_diary(bot_user, document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION)
        bot_user.refresh_from_db()

        reason = selection.check_common(bot_user)

        assert reason is not None and "safety" in reason, reason
