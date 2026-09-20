"""Анонс памяти на живом пути глобального бота (DRF-1292).

Обвязка — как в ``test_degraded_lines_1489``: подменена ТОЛЬКО модель, ход
идёт через ``handle_global_max_event`` целиком, «экран» — реально отправленное
сообщение. Писатели памяти настоящие (согласие PERSONAL_DATA есть,
``ayla_user_id`` задан, чтобы связь с Ayla не ходила в сеть).

Что проверяется:
* первый «я веган» — ответ консьержа + одна строка «Запомнила: …»;
* тот же факт вторым ходом — строки нет (один раз на факт);
* новое значение одиночного ключа (кето) — строка снова (supersede);
* «забудь, что я веган» — команда памяти: ничего не пишет и не объявляет;
* при анонсе вопрос памяти не вплетается (одна служебная строка на ход);
* сбойный ход («AI недоступна») строки не получает.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.quick_actions import AI_UNAVAILABLE_TEXT
from apps.consent.services import record_global_consent
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.models import MemoryEntry
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.llm.protocol import CompletionResult
from apps.orchestrator import concierge
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db(transaction=True)

ANSWER = "Хорошо, учту это при подборе."


@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )


@pytest.fixture(autouse=True)
def _no_intent_resolution(monkeypatch):
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())


@pytest.fixture
def sent(monkeypatch):
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


@pytest.fixture
def weave(monkeypatch):
    """Вопрос памяти — заглушка, которая всегда ХОЧЕТ спросить."""
    calls: list[str] = []

    def fake_weave(_conversation, _bot_user, reply):
        calls.append(reply.text)
        return type(reply)(
            text=f"{reply.text}\n\nКстати, чтобы подбирать точнее — когда тебе удобно?",
            action_data=reply.action_data,
            persisted=reply.persisted,
        )

    monkeypatch.setattr(max_handler, "maybe_weave_question", fake_weave)
    return calls


def _model(monkeypatch, text: str = ANSWER, *, raises: BaseException | None = None):
    provider = AsyncMock()
    if raises is not None:
        provider.complete.side_effect = raises
    else:
        provider.complete.return_value = CompletionResult(
            text=text,
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=5,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="stop",
        )
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)
    return provider


_UID = iter(range(72001, 72999))


def _person():
    from django.utils import timezone

    user_id = next(_UID)
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899", ayla_user_id=uuid.uuid4()
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf1292",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    resolve_active_global_conversation(bot_user)
    return bot_user, user_id


def _msg(*, text: str, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": 8899, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _turn(sent, user_id: int, text: str, n: int) -> str:
    max_handler.handle_global_max_event(_msg(text=text, user_id=user_id, mid=f"m{user_id}-{n}"))
    return sent[-1]["text"]


class TestAnnounceOnLivePath:
    def test_first_fact_is_announced_once_and_supersede_again(self, monkeypatch, sent, fake_redis):
        _model(monkeypatch)
        bot_user, uid = _person()

        first = _turn(sent, uid, "я веган", 1)
        assert first.startswith(ANSWER)
        assert "Запомнила: ты придерживаешься веганского питания." in first
        assert "«забудь про питание»" in first
        assert MemoryEntry.objects.filter(user_id=bot_user.ayla_user_id).count() == 1

        # Тот же факт — записи нет (дедуп), строки нет (один раз на факт).
        second = _turn(sent, uid, "я веган, если что", 2)
        assert second == ANSWER
        assert MemoryEntry.objects.filter(user_id=bot_user.ayla_user_id).count() == 1

        # Новое значение одиночного ключа — supersede — объявляется снова.
        third = _turn(sent, uid, "я теперь на кето", 3)
        assert "Запомнила: ты придерживаешься кето-диеты." in third

    def test_turn_without_a_fact_has_no_line(self, monkeypatch, sent, fake_redis):
        _model(monkeypatch)
        _, uid = _person()
        assert _turn(sent, uid, "мне бы совет", 1) == ANSWER

    def test_forget_command_writes_nothing_and_announces_nothing(
        self, monkeypatch, sent, fake_redis
    ):
        _model(monkeypatch)
        bot_user, uid = _person()
        _turn(sent, uid, "я веган", 1)

        text = _turn(sent, uid, "забудь про питание", 2)

        # Присутствие первым: это ответ команды памяти, не консьержа…
        assert "Готово — забыла" in text
        # …и в нём нет анонса (а первый ход его имел — сторож живой).
        assert "Запомнила" in sent[-2]["text"]
        assert "Запомнила" not in text
        live = MemoryEntry.objects.filter(
            user_id=bot_user.ayla_user_id, soft_deleted_at__isnull=True
        )
        assert live.count() == 0

    def test_announce_and_memory_question_never_share_a_turn(
        self, monkeypatch, sent, fake_redis, weave
    ):
        _model(monkeypatch)
        _, uid = _person()

        with_fact = _turn(sent, uid, "я веган", 1)
        assert "Запомнила" in with_fact
        assert "когда тебе удобно" not in with_fact
        assert weave == []  # вопрос даже не спрашивали

        plain = _turn(sent, uid, "мне бы совет", 2)
        assert plain.startswith(ANSWER)
        assert "когда тебе удобно" in plain  # вопрос — когда объявлять нечего
        assert "Запомнила" not in plain

    def test_outage_turn_gets_no_line_even_with_a_fact(self, monkeypatch, sent, fake_redis):
        _model(monkeypatch, raises=RuntimeError("provider down"))
        bot_user, uid = _person()
        _last_person_ayla_id = bot_user.ayla_user_id
        text = _turn(sent, uid, "я веган", 1)
        # Присутствие первым: экран сбоя, не ответ консьержа.
        assert text == AI_UNAVAILABLE_TEXT
        assert "Запомнила" not in text
        # Факт при этом записан после отправки — человек его сказал.
        assert MemoryEntry.objects.filter(user_id=_last_person_ayla_id).count() == 1

    def test_link_over_budget_writes_after_send_and_adds_no_line(
        self, monkeypatch, sent, fake_redis
    ):
        """Узел бюджета: связь с Ayla не уложилась в 1 с → строки нет, факт
        записан ПОСЛЕ отправки (как раньше), не потерян."""
        import time as _time

        from apps.orchestrator.memory import personal_context as pc

        _model(monkeypatch)
        user_id_holder = uuid.uuid4()
        # Человек ещё НЕ связан с Ayla — писатель обязан пойти в ensure_ayla_link.
        from django.utils import timezone

        uid = next(_UID)
        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=str(uid), chat_id="8899"
        )
        bot_user.welcomed_at = timezone.now()
        bot_user.save(update_fields=["welcomed_at"])
        record_global_consent(
            bot_user,
            consent_type="personal_data",
            source="test:drf1292",
            document_version="welcome-s2-v1",
        )
        resolve_active_global_conversation(bot_user)

        calls: list[float | None] = []

        def slow_link(_bot_user, *, trigger="unknown", timeout_s=None):
            calls.append(timeout_s)
            if timeout_s is not None:
                # Пре-отправка: «Ayla молчит» весь бюджет — и ничего.
                _time.sleep(timeout_s)
                return None
            # Пост-отправка, без бюджета: связь состоялась.
            return user_id_holder

        monkeypatch.setattr(pc, "ensure_ayla_link", slow_link)

        text = _turn(sent, uid, "я веган", 1)

        assert text == ANSWER  # строки нет: обещать «запомнила» до записи нельзя
        assert calls[0] == 1.0  # бюджет пре-отправки
        assert None in calls[1:]  # повтор после отправки — обычный таймаут клиента
        assert MemoryEntry.objects.filter(user_id=user_id_holder).count() == 1

    def test_writers_run_before_the_send(self, monkeypatch, sent, fake_redis):
        """Порядок: запись факта — ДО send_message, иначе строка обещала бы будущее."""
        from apps.orchestrator import memory_announce

        _model(monkeypatch)
        _, uid = _person()
        order: list[str] = []
        real_record = memory_announce.record_turn_facts

        def spy_record(*a, **kw):
            order.append(f"record:{kw.get('link_timeout_s')}")
            return real_record(*a, **kw)

        monkeypatch.setattr(max_handler, "record_turn_facts", spy_record)
        real_send = max_handler.send_message

        def spy_send(**kw):
            order.append("send")
            return real_send(**kw)

        monkeypatch.setattr(max_handler, "send_message", spy_send)

        _turn(sent, uid, "я веган", 1)

        assert order[:2] == ["record:1.0", "send"]
