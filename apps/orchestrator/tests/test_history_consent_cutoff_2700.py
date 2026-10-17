"""DRF-2700 — согласие проверяется в точке ЧТЕНИЯ истории.

Дефект, замеренный на ``c38b54b8``: отзыв согласия снимает его сразу, а
обезличивание переписки — отдельный шаг каскада. Если он падает (Redis
переписки недоступен), строки ``Message`` остаются целыми при уже снятом
согласии, и читатель истории отдавал их модели на следующих ходах. У человека
без связки с Ayla повтор шага при этом не взведён.

Правка закрывает выдачу ПОСТРОЕНИЕМ: реплики, сказанные до последнего отзыва
согласия, модели не отдаются — прошло обезличивание или нет.

* c1 — отсечка: откуда берётся и чего не захватывает;
* c2 — читатель: до отсечки, в момент отсечки, после неё; близнец без отзыва;
* c3 — отказ чтения отсечки закрывает историю, а не открывает;
* c4 — сквозной, через ``GlobalMaxHandler``: упавшее обезличивание, строка в
  базе цела, модели реплика не уходит; близнец без отзыва — уходит;
* c5 — новая выдача согласия прежние реплики не возвращает.

Данные синтетические.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.consent.models import ConsentRecord
from apps.consent.services import last_personal_data_withdrawal, record_global_consent
from apps.conversations.models import Message
from apps.conversations.services import (
    record_global_message,
    resolve_active_global_conversation,
)
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.llm.protocol import CompletionResult
from apps.orchestrator import concierge
from apps.orchestrator.concierge import GlobalConversationStore
from apps.tenancy.models import Tenant

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value
MARKER = "ZETA-MARKER-2700"

# Моменты — литералами: отсечка сравнивается с ними, а не с собой.
T_GRANT = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
T_WITHDRAW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _person(uid: str) -> BotUser:
    return resolve_or_create_global_bot_user(
        channel="max", channel_user_id=uid, chat_id=f"{uid}-chat"
    )


def _grant(bot_user: BotUser, *, consent_type: str = PD) -> ConsentRecord:
    return record_global_consent(
        bot_user, consent_type=consent_type, source="test-2700", document_version="v1"
    )


def _withdrawn(bot_user: BotUser, at: datetime, *, consent_type: str = PD) -> None:
    """Грант, отозванный в момент ``at`` — тем же полем, которое ставит ``withdraw``."""
    record = _grant(bot_user, consent_type=consent_type)
    ConsentRecord.all_tenants.filter(pk=record.pk).update(withdrawn_at=at)


def _say(conversation: Any, text: str, at: datetime, *, role: str = "assistant") -> Message:
    message = record_global_message(conversation, role=role, content=text)
    Message.all_tenants.filter(pk=message.pk).update(created_at=at)
    return message


def _texts(rows: list[Any]) -> list[str]:
    return [row.content for row in rows]


# ─── c1: отсечка ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCutoff:
    def test_c1_no_consent_rows_means_no_cutoff(self) -> None:
        assert last_personal_data_withdrawal(_person("2700-c1-none")) is None

    def test_c1_an_active_grant_is_not_a_cutoff(self) -> None:
        person = _person("2700-c1-grant")
        _grant(person)

        assert last_personal_data_withdrawal(person) is None

    def test_c1_the_cutoff_is_the_withdrawal_moment(self) -> None:
        person = _person("2700-c1-withdrawn")
        _withdrawn(person, T_WITHDRAW)

        assert last_personal_data_withdrawal(person) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def test_c1_the_latest_of_several_withdrawals_wins(self) -> None:
        person = _person("2700-c1-several")
        _withdrawn(person, datetime(2026, 9, 10, 12, 0, tzinfo=UTC))
        _withdrawn(person, datetime(2026, 9, 20, 8, 30, tzinfo=UTC))
        _withdrawn(person, datetime(2026, 9, 15, 0, 0, tzinfo=UTC))

        assert last_personal_data_withdrawal(person) == datetime(2026, 9, 20, 8, 30, tzinfo=UTC)

    def test_c1_a_new_grant_does_not_lift_the_cutoff(self) -> None:
        person = _person("2700-c1-regrant")
        _withdrawn(person, T_WITHDRAW)
        _grant(person)

        assert last_personal_data_withdrawal(person) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def test_c1_a_withdrawal_on_the_other_shell_of_the_person_counts(self) -> None:
        """Отзыв в Mini App стоит на одной строке, разговор чата — на другой."""
        chat = _person("2700-c1-shells")
        salon = Tenant.objects.create(slug="salon-2700-c1", name="Salon 2700")
        miniapp = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id="2700-c1-shells"
        )
        _withdrawn(miniapp, T_WITHDRAW)

        assert last_personal_data_withdrawal(chat) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def test_c1_another_persons_withdrawal_is_not_mine(self) -> None:
        mine = _person("2700-c1-mine")
        other = _person("2700-c1-other")
        _withdrawn(other, T_WITHDRAW)

        # Близнец: у самого отозвавшего отсечка есть — запрос не пуст вообще.
        assert last_personal_data_withdrawal(other) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        assert last_personal_data_withdrawal(mine) is None

    def test_c1_a_withdrawn_marketing_consent_is_not_a_cutoff(self) -> None:
        """Отказ от рассылки — не отзыв согласия на данные: историю он не закрывает."""
        person = _person("2700-c1-marketing")
        marketing = ConsentRecord.ConsentType.MARKETING.value
        _withdrawn(person, T_WITHDRAW, consent_type=marketing)

        assert ConsentRecord.all_tenants.filter(
            bot_user=person, consent_type=marketing, withdrawn_at__isnull=False
        ).exists()
        assert last_personal_data_withdrawal(person) is None


# ─── c2: читатель ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestReader:
    def _history(self, uid: str) -> tuple[BotUser, Any]:
        person = _person(uid)
        conversation = resolve_active_global_conversation(person)
        _say(conversation, "до отзыва — час", T_WITHDRAW - timedelta(hours=1))
        _say(conversation, "до отзыва — секунда", T_WITHDRAW - timedelta(seconds=1))
        _say(conversation, "ровно в момент отзыва", T_WITHDRAW)
        _say(conversation, "после отзыва — секунда", T_WITHDRAW + timedelta(seconds=1))
        _say(conversation, "после отзыва — день", T_WITHDRAW + timedelta(days=1))
        return person, conversation

    def test_c2_twin_without_a_withdrawal_every_row_is_read(self) -> None:
        _, conversation = self._history("2700-c2-open")

        rows = GlobalConversationStore().load_recent_history(conversation)

        assert _texts(rows) == [
            "до отзыва — час",
            "до отзыва — секунда",
            "ровно в момент отзыва",
            "после отзыва — секунда",
            "после отзыва — день",
        ]

    def test_c2_rows_at_or_before_the_withdrawal_are_not_read(self) -> None:
        person, conversation = self._history("2700-c2-closed")
        _withdrawn(person, T_WITHDRAW)

        rows = GlobalConversationStore().load_recent_history(conversation)

        assert _texts(rows) == ["после отзыва — секунда", "после отзыва — день"]
        # Строки при этом целы: закрыто чтение, а не хранение.
        assert Message.all_tenants.filter(conversation=conversation).count() == 5

    def test_c2_an_active_grant_alone_closes_nothing(self) -> None:
        person, conversation = self._history("2700-c2-granted")
        _grant(person)

        assert len(GlobalConversationStore().load_recent_history(conversation)) == 5

    def test_c2_the_limit_still_takes_the_newest_readable_rows(self) -> None:
        """Под отсечкой предел по-прежнему берёт самые свежие строки, по порядку.

        Чего этот узел НЕ различает: «отсечь, потом взять N» и «взять N, потом
        отсечь». Закрытые строки всегда старше открытых, поэтому результат у
        обоих порядков один — подмена порядка выживает, и она эквивалентная.
        """
        person = _person("2700-c2-limit")
        conversation = resolve_active_global_conversation(person)
        for i in range(4):
            _say(conversation, f"старое {i}", T_WITHDRAW - timedelta(minutes=10 - i))
        for i in range(3):
            _say(conversation, f"новое {i}", T_WITHDRAW + timedelta(minutes=1 + i))
        _withdrawn(person, T_WITHDRAW)

        store = GlobalConversationStore()

        assert _texts(store.load_recent_history(conversation, limit=10)) == [
            "новое 0",
            "новое 1",
            "новое 2",
        ]
        assert _texts(store.load_recent_history(conversation, limit=2)) == ["новое 1", "новое 2"]

    def test_c2_exclude_id_still_works_under_a_cutoff(self) -> None:
        person, conversation = self._history("2700-c2-exclude")
        _withdrawn(person, T_WITHDRAW)
        last = Message.all_tenants.get(conversation=conversation, content="после отзыва — день")

        rows = GlobalConversationStore().load_recent_history(conversation, exclude_id=last.id)

        assert _texts(rows) == ["после отзыва — секунда"]


# ─── c3: отказ чтения отсечки ────────────────────────────────────────────────


@pytest.mark.django_db
class TestFailClosed:
    def test_c3_a_failing_cutoff_read_withholds_the_whole_history(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        person = _person("2700-c3")
        conversation = resolve_active_global_conversation(person)
        _say(conversation, "реплика", T_WITHDRAW + timedelta(days=1))
        store = GlobalConversationStore()
        # Близнец: пока отсечка читается, строка отдаётся.
        assert _texts(store.load_recent_history(conversation)) == ["реплика"]

        def boom(bot_user: Any) -> None:
            raise RuntimeError("consent read failed")

        monkeypatch.setattr("apps.consent.services.last_personal_data_withdrawal", boom)

        assert store.load_recent_history(conversation) == []
        assert "orchestrator.concierge.history_consent_cutoff_failed" in caplog.text


# ─── c4, c5: сквозной через обработчик канала ────────────────────────────────


def _entry(text: str, user_id: int) -> dict[str, str]:
    payload = {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Probe"},
            "recipient": {"chat_id": user_id + 1, "chat_type": "dialog"},
            "body": {"mid": f"m-{uuid.uuid4()}", "seq": 1, "text": text, "attachments": []},
        },
    }
    return {"data": json.dumps(payload), "trace_id": str(uuid.uuid4()), "resolved_tenant_id": ""}


def _prose(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        prompt_tokens=20,
        completion_tokens=8,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


class _Chat:
    """Разговор через ``GlobalMaxHandler`` с подменённой моделью.

    ``say`` возвращает всё, что на этом ходе ушло модели, одной строкой. Ход
    может сделать больше одного вызова (консьерж и проход извлечения фактов
    под согласием) — каждый из них выход к модели, поэтому судится ход целиком.
    Ответ задаётся на ход, а не очередью: число вызовов за ход узлу не важно.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, user_id: int) -> None:
        from apps.conversations.tests.test_erasure import _FakeRedis
        from apps.llm import pii_tokenizer
        from apps.orchestrator.decision_readiness import state as dre_state
        from apps.orchestrator.memory import short_term

        self.redis = _FakeRedis()
        monkeypatch.setattr(dre_state, "_redis_client", lambda: self.redis)
        monkeypatch.setattr(short_term, "_redis_client", lambda: self.redis)
        monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: self.redis)
        monkeypatch.setattr(
            max_handler,
            "send_message",
            lambda *, chat_id, text, attachments=None, timeout=10.0: {"ok": True},
        )
        self._answer = "Ответ."
        self.provider = AsyncMock()
        self.provider.complete.side_effect = lambda *a, **k: _prose(self._answer)
        router = Mock()
        router.get_provider.return_value = self.provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)
        self.user_id = user_id

    def say(self, text: str, *, answer: str = "Ответ.") -> str:
        self._answer = answer
        before = self.provider.complete.call_count
        GlobalMaxHandler()(_entry(text, self.user_id))
        calls = self.provider.complete.call_args_list[before:]
        assert calls, "ход не дошёл до модели"
        return json.dumps([call.args[0] for call in calls], ensure_ascii=False, default=str)

    @property
    def bot_user(self) -> BotUser:
        return BotUser.all_tenants.get(channel="max", channel_user_id=str(self.user_id))


def _revoke_with_failed_anonymisation(monkeypatch: pytest.MonkeyPatch, bot_user: BotUser) -> Any:
    """Продуктовый отзыв, при котором обезличивание переписки падает."""
    from apps.consent.customer import revoke_data_storage
    from apps.conversations import erasure

    def down(conversation_id: Any) -> None:
        raise ConnectionError("dialogue redis down")

    with monkeypatch.context() as patched:
        patched.setattr(erasure, "_clear_redis_stores", down)
        return revoke_data_storage(bot_user)


@pytest.mark.django_db(transaction=True)
class TestThroughTheChannelHandler:
    def test_c4_twin_without_a_withdrawal_the_earlier_reply_reaches_the_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _Chat(monkeypatch, 2700401)
        chat.say("привет", answer=f"Помню, что ты {MARKER}.")
        _grant(chat.bot_user)

        sent = chat.say("что посоветуешь на ужин?")

        assert MARKER in sent

    def test_c4_after_a_withdrawal_with_failed_anonymisation_it_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _Chat(monkeypatch, 2700402)
        chat.say("привет", answer=f"Помню, что ты {MARKER}.")
        _grant(chat.bot_user)
        assert MARKER in chat.say("что ты обо мне помнишь?")  # до отзыва — уходит

        result = _revoke_with_failed_anonymisation(monkeypatch, chat.bot_user)

        # Это именно окно сбоя: согласие снято, шаг обезличивания не прошёл,
        # строка с репликой в базе ЦЕЛА, у человека нет связки с Ayla.
        steps = {step.step: step.ok for step in result.steps}
        assert steps["consent_withdraw"] is True
        assert steps["dialogue_anonymize"] is False
        bot_user = chat.bot_user
        assert bot_user.ayla_user_id is None
        assert (
            Message.all_tenants.filter(
                conversation__bot_user=bot_user, content__contains=MARKER
            ).count()
            == 1
        )
        # И окно Redis цело — его очистка и была тем, что упало.
        assert MARKER in json.dumps(chat.redis.store, ensure_ascii=False, default=str)

        first = chat.say("а что посоветуешь на ужин?", answer="Ответ ПОСЛЕ-ОТЗЫВА.")
        second = chat.say("и ещё вопрос")

        assert MARKER not in first
        assert MARKER not in second
        # Реплики ПОСЛЕ отзыва — снова обычная история: закрыто прошлое, а не разговор.
        assert "Ответ ПОСЛЕ-ОТЗЫВА." in second

    def test_c5_a_new_grant_does_not_bring_the_old_replies_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _Chat(monkeypatch, 2700501)
        chat.say("привет", answer=f"Помню, что ты {MARKER}.")
        _grant(chat.bot_user)
        assert MARKER in chat.say("что ты обо мне помнишь?")
        _revoke_with_failed_anonymisation(monkeypatch, chat.bot_user)

        _grant(chat.bot_user)  # человек согласился заново
        chat.say("я снова здесь", answer="Ответ ПОСЛЕ-СОГЛАСИЯ.")
        sent = chat.say("что было раньше?")

        assert MARKER not in sent
        assert "Ответ ПОСЛЕ-СОГЛАСИЯ." in sent
