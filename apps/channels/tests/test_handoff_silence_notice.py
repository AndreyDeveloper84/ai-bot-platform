"""Молчание объяснено, и индикаторы не обещают того, чего не будет.

DRF-1486 + DRF-1487, живой сбой 04.09.2026. Клиент написал салонному
боту, ему сказали «передаю менеджеру», задачу завели на салонный диалог —
а онемел ВИТРИННЫЙ бот, другой, в другом чате, ничего про оператора не
говоривший. 1 ч 24 мин, пять сообщений, ноль ответов. И на каждое из этих
пяти уходили «прочитано» и «печатает…»: молчащий бот читался как занятый,
а бот, показавший «печатает», обещал ответ — пять раз подряд.

Что здесь пришпилено:

* **Одно сообщение на эпизод, не одно на входящее.** Второе и третье
  входящее в замьюченном диалоге не получают ничего — иначе объяснение
  само становится спамом.
* **Две формулировки, каждая правдива.** Диалог, который сам сказал
  «передаю менеджеру», и диалог, куда молчание переехало, читают разное.
  Отличить их по одной базе нельзя (задача в обоих случаях может лежать
  на салонном диалоге), поэтому факт доставки подтверждения пишется там,
  где подтверждение доставляется.
* **Снятие mute — тоже событие.** Бот перестал молча оживать.
* **Индикаторы после решения отвечать.** У MAX нет ``typing_off``
  (``outbound._CHAT_ACTIONS``), снять «печатает…» нечем — значит,
  единственная честная правка — не отправлять его тому, кому не ответят.

Каждая отрицательная проверка идёт в паре с положительной на тех же
данных: «ноль индикаторов в замьюченном диалоге» бессмысленно без «в
обычном их два» рядом (DRF-1411).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation, Message
from apps.handoff.models import AdminTask, HandoffSilenceNotice
from apps.handoff.services import resolve_admin_task
from apps.handoff.silence import (
    RELEASE_ACTION_TYPE,
    SILENCE_ACTION_TYPE,
    SILENCE_ANNOUNCED_HERE_TEXT,
    SILENCE_RELEASED_TEXT,
    SILENCE_TRANSFERRED_TEXT,
)
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

USER_ID = 4242
CHAT_ID = 4242


def _msg(text: str, *, user_id: int = USER_ID, chat_id: int = CHAT_ID, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run_global(text: str, *, mid: str) -> None:
    max_handler.handle_global_max_event(_msg(text, mid=mid), trace_id=str(uuid.uuid4()))


@pytest.fixture
def sent(monkeypatch):
    """Every outbound message, wherever it was sent from.

    Both the handler's own replies (``max_handler.send_message``) and the
    notices (``apps.handoff.silence`` → ``outbound.send_message``) land in
    one list: what the person actually received, in order.
    """

    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0, bot=None):
        calls.append({"chat_id": str(chat_id), "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    monkeypatch.setattr("apps.channels.max.outbound.send_message", fake_send)
    return calls


@pytest.fixture
def actions(monkeypatch):
    """Every chat indicator, in order."""

    fired: list[str] = []
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action",
        lambda **kw: fired.append(kw.get("action")),
    )
    return fired


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def concierge(monkeypatch):
    spy = MagicMock()
    from apps.orchestrator.discovery import DiscoveryReply

    spy.return_value = DiscoveryReply(text="Какая услуга интересует?")
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    return spy


def _global_conversation() -> Conversation:
    bot_user = BotUser.all_tenants.get(
        channel="max",
        channel_user_id=str(USER_ID),
        tenant__slug=GLOBAL_BOT_TENANT_SLUG,
    )
    return Conversation.all_tenants.get(bot_user=bot_user, tenant__slug=GLOBAL_BOT_TENANT_SLUG)


def _salon_handoff_elsewhere(slug: str = "salon-elsewhere") -> AdminTask:
    """Reproduce the pilot: the handoff was raised in a SALON dialog.

    Nothing in the global dialog ever mentioned an operator — that is the
    whole point of the 04.09 failure, so the fixture must not cheat by
    routing through the global path.
    """

    tenant = Tenant.objects.create(slug=slug, name=slug.upper())
    with tenant_scope(tenant):
        salon_user = BotUser.objects.create(
            tenant=tenant, channel="max", channel_user_id=str(USER_ID)
        )
        salon_conv = Conversation.all_tenants.create(tenant=tenant, bot_user=salon_user)
        from apps.handoff.services import create_admin_task

        return create_admin_task(
            salon_conv,
            task_type=AdminTask.TaskType.HANDOFF,
            reason="клиент попросил человека в салонном боте",
        )


# --------------------------------------------------------------------------- #
# DRF-1486 — молчание объяснено                                                #
# --------------------------------------------------------------------------- #
class TestSilenceIsExplainedOnce:
    def test_transferred_mute_explains_where_it_came_from(
        self, sent, actions, fake_redis, concierge
    ):
        """Молчание переехало с салонного бота — формулировка объясняет именно это."""

        task = _salon_handoff_elsewhere()
        assert task.status == AdminTask.Status.OPEN  # presence: эпизод действительно открыт

        _run_global("вы тут?", mid="tr-1")

        assert [c["text"] for c in sent] == [SILENCE_TRANSFERRED_TEXT]
        assert sent[0]["chat_id"] == str(CHAT_ID)
        # Никаких идентификаторов задачи, тенанта и очереди человеку.
        body = sent[0]["text"]
        assert str(task.id) not in body
        assert task.tenant.slug not in body
        assert "duty" not in body

    def test_second_and_third_message_get_nothing(self, sent, actions, fake_redis, concierge):
        """Ровно одно уведомление на эпизод — не одно на входящее."""

        _salon_handoff_elsewhere()

        _run_global("вы тут?", mid="rep-1")
        assert len(sent) == 1, "первое замьюченное входящее обязано получить объяснение"

        _run_global("алло?", mid="rep-2")
        _run_global("ну что же вы", mid="rep-3")
        assert len(sent) == 1
        notice = HandoffSilenceNotice.objects.get(conversation=_global_conversation())
        assert notice.silence_notified_at is not None

    def test_dialog_that_announced_the_handoff_reads_the_other_wording(
        self, sent, actions, fake_redis, concierge
    ):
        """Здесь человеку уже сказали «передаю менеджеру» — текст другой."""

        _run_global("позовите оператора", mid="own-1")
        assert sent[-1]["text"] == "Передаю менеджеру — ответят в течение 30 минут."

        _run_global("вы тут?", mid="own-2")
        assert sent[-1]["text"] == SILENCE_ANNOUNCED_HERE_TEXT
        assert SILENCE_TRANSFERRED_TEXT not in [c["text"] for c in sent]

    def test_notice_is_in_the_transcript_but_not_in_short_term_memory(
        self, sent, actions, fake_redis, concierge
    ):
        """Оператор видит, что клиент прочитал; консьерж этим не грунтуется."""

        _salon_handoff_elsewhere()
        _run_global("вы тут?", mid="tx-1")

        conv = _global_conversation()
        stored = Message.all_tenants.filter(conversation=conv, action_type=SILENCE_ACTION_TYPE)
        assert stored.count() == 1
        assert stored.first().content == SILENCE_TRANSFERRED_TEXT
        remembered = [m["content"] for m in short_term.recall(conv.id)]
        # Присутствие: короткая память вообще наполнена этим ходом — иначе
        # «объяснения там нет» доказывало бы только, что памяти нет.
        assert "вы тут?" in remembered
        assert SILENCE_TRANSFERRED_TEXT not in remembered

    def test_dialog_without_an_open_task_answers_as_before(
        self, sent, actions, fake_redis, concierge
    ):
        """Положительная стража: обычный ход не трогается вообще."""

        _run_global("хочу стрижку", mid="ok-1")

        assert [c["text"] for c in sent] == ["Какая услуга интересует?"]
        assert HandoffSilenceNotice.objects.count() == 0
        assert concierge.call_count == 1


class TestReleaseIsAnnounced:
    """``django_capture_on_commit_callbacks`` — не удобство, а контракт.

    Сообщение «бот снова на связи» уходит ``on_commit`` по той же причине,
    по которой так уходит уведомление о создании задачи (DRF-1029 §3.2):
    откатившееся закрытие не должно сообщать клиенту, что бот вернулся.
    Тест обязан закрывать задачу тем же способом, каким это делает
    продакшн, — то есть дожидаясь коммита.
    """

    def test_closing_the_task_tells_the_person_the_bot_is_back(
        self, sent, actions, fake_redis, concierge, django_capture_on_commit_callbacks
    ):
        task = _salon_handoff_elsewhere()
        _run_global("вы тут?", mid="rel-1")
        assert sent[-1]["text"] == SILENCE_TRANSFERRED_TEXT

        with django_capture_on_commit_callbacks(execute=True), tenant_scope(task.tenant):
            resolve_admin_task(task, resolution_note="ответили клиенту")

        assert sent[-1]["text"] == SILENCE_RELEASED_TEXT
        assert [c["text"] for c in sent].count(SILENCE_RELEASED_TEXT) == 1
        notice = HandoffSilenceNotice.objects.get(conversation=_global_conversation())
        assert notice.released_at is not None
        assert Message.all_tenants.filter(action_type=RELEASE_ACTION_TYPE).count() == 1

    def test_bot_answers_again_after_the_release(
        self, sent, actions, fake_redis, concierge, django_capture_on_commit_callbacks
    ):
        """Положительная стража к предыдущему: бот не просто попрощался — он вернулся."""

        task = _salon_handoff_elsewhere()
        _run_global("вы тут?", mid="back-1")
        with django_capture_on_commit_callbacks(execute=True), tenant_scope(task.tenant):
            resolve_admin_task(task, resolution_note="done")

        _run_global("тогда подскажите про массаж", mid="back-2")

        assert sent[-1]["text"] == "Какая услуга интересует?"
        assert concierge.call_count == 1

    def test_second_task_still_open_keeps_the_dialog_quiet(
        self, sent, actions, fake_redis, concierge, django_capture_on_commit_callbacks
    ):
        """Закрыли одну задачу, вторая держит человека — «бот вернулся» не врёт."""

        first = _salon_handoff_elsewhere("salon-one")
        _salon_handoff_elsewhere("salon-two")
        _run_global("вы тут?", mid="two-1")
        assert sent[-1]["text"] == SILENCE_TRANSFERRED_TEXT

        with django_capture_on_commit_callbacks(execute=True), tenant_scope(first.tenant):
            resolve_admin_task(first, resolution_note="одна из двух")

        assert SILENCE_RELEASED_TEXT not in [c["text"] for c in sent]
        notice = HandoffSilenceNotice.objects.get(conversation=_global_conversation())
        assert notice.released_at is None

    def test_a_new_episode_gets_its_own_single_notice(
        self, sent, actions, fake_redis, concierge, django_capture_on_commit_callbacks
    ):
        """Эпизод закрылся — следующий mute снова объясняется, ровно один раз."""

        first = _salon_handoff_elsewhere("salon-ep1")
        _run_global("вы тут?", mid="ep-1")
        with django_capture_on_commit_callbacks(execute=True), tenant_scope(first.tenant):
            resolve_admin_task(first, resolution_note="эпизод 1")
        said_after_first = [c["text"] for c in sent].count(SILENCE_TRANSFERRED_TEXT)
        assert said_after_first == 1

        _salon_handoff_elsewhere("salon-ep2")
        _run_global("снова вы тут?", mid="ep-2")
        _run_global("и ещё раз", mid="ep-3")

        assert [c["text"] for c in sent].count(SILENCE_TRANSFERRED_TEXT) == 2


# --------------------------------------------------------------------------- #
# DRF-1487 — индикаторы                                                        #
# --------------------------------------------------------------------------- #
class TestIndicatorsFollowTheDecisionToAnswer:
    def test_muted_global_turn_fires_no_indicators(self, sent, actions, fake_redis, concierge):
        _salon_handoff_elsewhere()

        _run_global("вы тут?", mid="ind-1")

        assert actions == []  # empty-assert-ok: парная положительная проверка ниже
        # Положительная стража на ТЕХ ЖЕ данных: молчание действительно
        # случилось (иначе «ноль индикаторов» ничего не доказывает).
        assert sent[-1]["text"] == SILENCE_TRANSFERRED_TEXT

    def test_ordinary_global_turn_still_gets_both_indicators(
        self, sent, actions, fake_redis, concierge
    ):
        _run_global("хочу стрижку", mid="ind-2")

        assert actions == ["mark_seen", "typing_on"]
        assert sent[-1]["text"] == "Какая услуга интересует?"

    def test_five_muted_messages_five_times_nothing(self, sent, actions, fake_redis, concierge):
        """Замер 04.09: пять входящих — пять пар индикаторов и ноль ответов."""

        _salon_handoff_elsewhere()
        for i in range(5):
            _run_global(f"сообщение {i}", mid=f"ind-rep-{i}")

        assert actions == []  # empty-assert-ok: присутствие доказано строкой ниже
        assert len(sent) == 1 and sent[0]["text"] == SILENCE_TRANSFERRED_TEXT

    def test_indicators_precede_the_concierge(self, sent, actions, fake_redis, monkeypatch):
        """Перенос вниз не должен был спрятать индикаторы ЗА модель."""

        order: list[str] = []
        monkeypatch.setattr(
            "apps.channels.max.outbound.send_chat_action",
            lambda **kw: order.append(f"action:{kw.get('action')}"),
        )

        from apps.orchestrator.discovery import DiscoveryReply

        def _brain(*args, **kwargs):
            order.append("concierge")
            return DiscoveryReply(text="Какая услуга интересует?")

        monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", _brain)
        monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())

        _run_global("хочу стрижку", mid="ind-order")

        assert order == ["action:mark_seen", "action:typing_on", "concierge"]


class TestIndicatorsOnThePerTenantPath:
    """Вторая точка индикаторов (арендаторский путь) — тот же вопрос."""

    def _tenant(self) -> Tenant:
        return Tenant.objects.create(slug="salon-ind", name="Salon IND")

    def _run(self, tenant: Tenant, text: str, *, mid: str) -> None:
        with tenant_scope(tenant), trace_id_scope(str(uuid.uuid4())):
            max_handler.handle_max_event(
                _msg(text, user_id=7007, chat_id=7007, mid=mid), trace_id=uuid.uuid4()
            )

    def test_muted_tenant_turn_fires_no_indicators(self, sent, actions, fake_redis, concierge):
        tenant = self._tenant()
        self._run(tenant, "привет", mid="pt-warm")
        assert actions == ["mark_seen", "typing_on"], "обычный ход обязан получить индикаторы"
        answered_before_mute = len(sent)
        assert answered_before_mute >= 1

        conv = Conversation.all_tenants.get(bot_user__channel_user_id="7007", tenant=tenant)
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
        actions.clear()

        self._run(tenant, "вы тут?", mid="pt-muted")

        assert actions == []  # empty-assert-ok: молчание доказано строкой ниже
        assert len(sent) == answered_before_mute, "бот действительно промолчал"

    def test_tenant_turn_out_of_handoff_keeps_indicators(
        self, sent, actions, fake_redis, concierge
    ):
        """Положительная стража на тех же данных: снятие handoff возвращает индикаторы."""

        tenant = self._tenant()
        self._run(tenant, "привет", mid="pt-a")
        conv = Conversation.all_tenants.get(bot_user__channel_user_id="7007", tenant=tenant)
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
        self._run(tenant, "молчим", mid="pt-b")
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.IDLE)
        actions.clear()

        self._run(tenant, "и снова здравствуйте", mid="pt-c")

        assert actions == ["mark_seen", "typing_on"]
