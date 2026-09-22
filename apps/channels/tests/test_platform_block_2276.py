"""DRF-2276 — блокировка клиента оператором платформы (решение владельца, CD §72 п.15).

Блокировка с причиной, автором, журналом и снятием есть с DRF-1497; её
единственным эффектом был забор на выходе ``max/outbound.send_message``.
Здесь — вход:

* заблокированный получает вежливую фразу **раз за эпизод блокировки** и
  дальше ничего: ни навыков, ни модели;
* **кризис и неотложка ему отвечаются всё равно (N-1, CD §67)** — гейт
  раньше блокировки. До этой задачи забор на выходе глушил и их: кризисный
  ответ заблокированному в MAX не уходил. Поэтому MAX-узлы здесь идут через
  настоящий ``send_message`` (подменена только сеть), а не через подмену
  ``handler.send_message`` — иначе забор в проверку не попадает;
* забор на выходе остаётся для всего прочего (напоминания, проактив);
* незаблокированный — без изменений.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from django.contrib.auth import get_user_model

from apps.channels.max import handler as max_handler
from apps.channels.telegram import handler as tg_handler
from apps.channels.telegram import outbound as tg_outbound
from apps.identity.models import BotUser
from apps.identity.services.blocking import BLOCK_NOTICE_TEXT, block_user, unblock_user
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

RED_FLAG = "онемела половина лица"
CRISIS = "я думаю о суициде"
CONCIERGE_TEXT = "Какая услуга интересует?"


@pytest.fixture(autouse=True)
def _harness(monkeypatch):
    import apps.channels.max.outbound as outbound
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)
    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def wire(monkeypatch, settings):
    """Сеть MAX — перехват ПОД забором: ``send_message`` работает настоящий."""
    import apps.channels.max.outbound as outbound

    settings.MAX_BOT_TOKEN = "test-token-2276"  # pragma: allowlist secret
    settings.MAX_API_BASE = "https://botapi.max.ru"
    texts: list[str] = []

    def fake_post(url, *, headers=None, params=None, json=None, timeout=None):  # noqa: ANN001, ANN202
        if str(url).endswith("/messages"):
            texts.append((json or {}).get("text", ""))
        return httpx.Response(200, json={"message": {}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(outbound.httpx, "post", fake_post)
    return texts


@pytest.fixture
def spy_concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text=CONCIERGE_TEXT))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def actor(db):  # noqa: ANN001, ANN201
    return get_user_model().objects.create_user("i.operator-2276")


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


def _block(actor, user_id: int, **filters) -> BotUser:  # noqa: ANN001
    row = BotUser.all_tenants.get(channel="max", channel_user_id=str(user_id), **filters)
    return block_user(actor=actor, bot_user=row, reason="шлёт рекламный спам в чат")


def _per_tenant(tenant: Tenant, user_id: int):  # noqa: ANN202
    def run(text: str, mid: str) -> None:
        trace = uuid.uuid4()
        with tenant_scope(tenant), trace_id_scope(str(trace)):
            max_handler.handle_max_event(_msg(text, user_id=user_id, mid=mid), trace_id=trace)

    return run


# ── глобальный MAX ───────────────────────────────────────────────────


class TestGlobal:
    def test_a_blocked_person_gets_the_notice_once_and_nothing_else(
        self, wire, spy_concierge, actor
    ) -> None:
        _run_global("привет", user_id=7601, mid="a")
        assert wire == [CONCIERGE_TEXT]  # положительно: до блокировки бот отвечает
        _block(actor, 7601)
        spy_concierge.reset_mock()

        _run_global("почему молчите?", user_id=7601, mid="b")
        _run_global("ау", user_id=7601, mid="c")

        assert wire[1:] == [BLOCK_NOTICE_TEXT]  # раз за эпизод, второе — тишина
        spy_concierge.assert_not_called()

    def test_crisis_reaches_a_blocked_person(self, wire, spy_concierge, actor) -> None:
        """N-1: живая дыра до DRF-2276 — забор на выходе глушил и кризис."""
        _run_global("привет", user_id=7602, mid="a")
        _block(actor, 7602)

        _run_global(CRISIS, user_id=7602, mid="b")

        assert wire[-1] == CRISIS_REPLY_TEXT
        assert BLOCK_NOTICE_TEXT not in wire

    def test_a_classifier_red_flag_reaches_a_blocked_person(
        self, wire, spy_concierge, actor
    ) -> None:
        _run_global("привет", user_id=7603, mid="a")
        _block(actor, 7603)

        _run_global(RED_FLAG, user_id=7603, mid="b")

        assert wire[-1] == MEDICAL_EMERGENCY_TEXT_V2
        assert BLOCK_NOTICE_TEXT not in wire

    def test_a_block_on_a_salon_row_holds_on_the_global_bot(
        self, wire, spy_concierge, actor
    ) -> None:
        """Блокировка — платформенная: строка салона глушит и витринного бота."""
        salon = Tenant.objects.create(slug="blk-2276-salon", name="S")
        BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id="7604", chat_id="salon-7604"
        )
        _block(actor, 7604, tenant=salon)

        _run_global("привет", user_id=7604, mid="a")

        assert wire == [BLOCK_NOTICE_TEXT]
        spy_concierge.assert_not_called()

    def test_unblock_restores_replies_and_a_new_block_is_a_new_episode(
        self, wire, spy_concierge, actor
    ) -> None:
        _run_global("привет", user_id=7605, mid="a")
        row = _block(actor, 7605)
        _run_global("ау", user_id=7605, mid="b")
        assert wire[-1] == BLOCK_NOTICE_TEXT

        unblock_user(actor=actor, bot_user=row, reason="разобрались, это был он сам")
        _run_global("снова здравствуйте", user_id=7605, mid="c")
        assert wire[-1] == CONCIERGE_TEXT  # снятие вернуло ответы

        block_user(actor=actor, bot_user=row, reason="снова шлёт рекламу в чат")
        _run_global("ау", user_id=7605, mid="d")
        assert wire[-1] == BLOCK_NOTICE_TEXT  # новый эпизод — фраза снова одна
        assert wire.count(BLOCK_NOTICE_TEXT) == 2

    def test_an_unblocked_person_is_unaffected(self, wire, spy_concierge, actor) -> None:
        """Контроль: соседний заблокированный не задевает незаблокированного."""
        _run_global("привет", user_id=7606, mid="a")
        _block(actor, 7606)

        _run_global("привет", user_id=7607, mid="a")

        assert wire[-1] == CONCIERGE_TEXT
        spy_concierge.assert_called()


# ── per-tenant MAX ───────────────────────────────────────────────────


class TestPerTenant:
    def test_notice_once_then_silence(self, wire, actor, monkeypatch) -> None:
        from unittest.mock import MagicMock

        tenant = Tenant.objects.create(slug="blk-2276-pt", name="T")
        run = _per_tenant(tenant, 7611)
        run("привет", "a")
        assert wire  # положительно: до блокировки бот ответил
        _block(actor, 7611, tenant=tenant)
        wire.clear()
        dispatch = MagicMock(side_effect=AssertionError("навыки заблокированному не работают"))
        monkeypatch.setattr(max_handler, "orchestrate_turn", dispatch)

        run("почему молчите?", "b")
        run("ау", "c")

        assert wire == [BLOCK_NOTICE_TEXT]
        dispatch.assert_not_called()

    def test_crisis_reaches_a_blocked_person(self, wire, actor) -> None:
        tenant = Tenant.objects.create(slug="blk-2276-pt-cr", name="T")
        run = _per_tenant(tenant, 7612)
        run("привет", "a")
        _block(actor, 7612, tenant=tenant)
        wire.clear()

        run(CRISIS, "b")

        assert wire == [CRISIS_REPLY_TEXT]

    def test_a_classifier_red_flag_reaches_a_blocked_person(self, wire, actor) -> None:
        tenant = Tenant.objects.create(slug="blk-2276-pt-rf", name="T")
        run = _per_tenant(tenant, 7613)
        run("привет", "a")
        _block(actor, 7613, tenant=tenant)
        wire.clear()

        run(RED_FLAG, "b")

        assert wire == [MEDICAL_EMERGENCY_TEXT_V2]


# ── Telegram ─────────────────────────────────────────────────────────


SKILL_TEXT = "Ответ навыка"


@pytest.fixture
def skills(monkeypatch):
    """Мозг хода per-tenant — шпион: навык либо вызван, либо нет, без модели."""
    from unittest.mock import MagicMock

    from apps.orchestrator.turn_seam import TurnReply

    spy = MagicMock(return_value=TurnReply(reply_text=SKILL_TEXT, action_type="faq"))
    monkeypatch.setattr(tg_handler, "orchestrate_turn", spy)
    return spy


class TestTelegram:
    @staticmethod
    def _run(tenant: Tenant, texts: list[str], *, block_after_first: bool, actor) -> list[str]:  # noqa: ANN001
        def payload(t: str, message_id: int) -> dict:
            return {
                "update_id": 9000 + message_id,
                "message": {
                    "message_id": message_id,
                    "date": 1731320000,
                    "from": {"id": 7621, "is_bot": False, "first_name": "Иван"},
                    "chat": {"id": 7621, "type": "private"},
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
            if block_after_first:
                row = BotUser.all_tenants.get(tenant=tenant, channel="telegram")
                block_user(actor=actor, bot_user=row, reason="шлёт рекламный спам в чат")
            post.reset_mock()
            for i, t in enumerate(texts, start=2):
                with tenant_scope(tenant):
                    tg_handler.handle_inbound(payload(t, i), tenant=tenant)
            return [
                c.kwargs["json"]["text"] for c in post.call_args_list if "sendMessage" in c.args[0]
            ]

    @staticmethod
    def _tenant(slug: str) -> Tenant:
        return Tenant.objects.create(
            slug=slug,
            name="TG",
            telegram_bot_token=f"bot-token-{slug}",  # pragma: allowlist secret
            telegram_webhook_secret=f"secret-{slug}",  # pragma: allowlist secret
        )

    def test_notice_once_then_silence(self, actor, skills) -> None:
        sent = self._run(
            self._tenant("blk-2276-tg"),
            ["почему молчите?", "ау"],
            block_after_first=True,
            actor=actor,
        )
        assert sent == [BLOCK_NOTICE_TEXT]
        assert skills.call_count == 1  # только «привет» до блокировки

    def test_crisis_and_red_flag_reach_a_blocked_person(self, actor, skills) -> None:
        sent = self._run(
            self._tenant("blk-2276-tg-cr"),
            [CRISIS, RED_FLAG],
            block_after_first=True,
            actor=actor,
        )
        assert sent == [CRISIS_REPLY_TEXT, MEDICAL_EMERGENCY_TEXT_V2]
        assert skills.call_count == 1  # red flag ответил гейт, не навык

    def test_an_unblocked_person_is_answered(self, actor, skills) -> None:
        """Контроль: без блокировки обычная реплика получает обычный ответ."""
        sent = self._run(
            self._tenant("blk-2276-tg-free"),
            ["почему молчите?"],
            block_after_first=False,
            actor=actor,
        )
        assert sent == [SKILL_TEXT]
        assert skills.call_count == 2


# ── забор на выходе ──────────────────────────────────────────────────


class TestOutboundFence:
    def test_a_reminder_to_a_blocked_person_is_still_suppressed(self, wire, actor) -> None:
        from apps.channels.max.outbound import send_message

        salon = Tenant.objects.create(slug="blk-2276-fence", name="S")
        row = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id="7631", chat_id="chat-7631"
        )
        free = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id="7632", chat_id="chat-7632"
        )
        block_user(actor=actor, bot_user=row, reason="шлёт рекламный спам в чат")

        assert send_message(user_id=free.channel_user_id, text="напоминание") == {"message": {}}
        assert send_message(user_id=row.channel_user_id, text="напоминание") == {"blocked": True}
        assert send_message(chat_id=row.chat_id, text="напоминание") == {"blocked": True}
        assert wire == ["напоминание"]  # ушло только незаблокированному

    def test_the_bypass_is_named_and_only_for_safety_or_the_notice(self, wire, actor) -> None:
        from apps.channels.max.outbound import send_message

        salon = Tenant.objects.create(slug="blk-2276-bypass", name="S")
        row = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id="7633", chat_id="chat-7633"
        )
        block_user(actor=actor, bot_user=row, reason="шлёт рекламный спам в чат")

        send_message(chat_id=row.chat_id, text=CRISIS_REPLY_TEXT, bypass_block="safety")
        send_message(chat_id=row.chat_id, text=BLOCK_NOTICE_TEXT, bypass_block="block_notice")
        assert wire == [CRISIS_REPLY_TEXT, BLOCK_NOTICE_TEXT]

        with pytest.raises(ValueError, match="bypass_block"):
            send_message(chat_id=row.chat_id, text="акция", bypass_block="promo")
        assert wire == [CRISIS_REPLY_TEXT, BLOCK_NOTICE_TEXT]  # промо не ушло
