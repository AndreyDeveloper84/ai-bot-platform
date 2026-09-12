"""«Бот знает, что спросил» — открытый вопрос и ответ на него (DRF-1779).

Приёмочный материал — диалог владельца 12.09 03:01–03:09 UTC (DRF-1754):
бот задал два вопроса скрининга, человек ответил «1. Спина, 2. После
работы» — и получил «Не разобрала эту фразу»; на «ну я назвал… спина» и
«и?» — «Сейчас проверю», «запускаю проверку». Модель на каждом ходу выбирала
``health_screening``, исполнитель отказывал по памятке DRF-1542, в чат уходила
проза рядом с несработавшим вызовом.

Здесь та же беседа идёт через НАСТОЯЩИЙ канал (обвязка — как в
``test_degraded_lines_1489``). Подменена только модель, и подменена ЧЕСТНО:
заглушка вызывает ``health_screening``, когда он ей предложен и в реплике
названа боль, — ровно так вела себя живая модель; иначе отвечает прозой.
Так тест не может пройти оттого, что заглушка «не захотела» вызвать
инструмент: если инструмент предложен на ходу ответа, она его вызовет, и
на экран уйдёт «Не разобрала» — как 12.09.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from django.utils import timezone

from apps.channels.max import handler as max_handler
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, open_question
from apps.orchestrator.llm import templates
from apps.orchestrator.memory import short_term
from apps.skills.health_screening.skill import SOFT_PAIN_REPLY

# ``transaction=True`` — ход консьержа пишет в БД из другого потока
# (``asyncio.run``), сессия в одной транзакции его не видит.
pytestmark = pytest.mark.django_db(transaction=True)

OWNER_TURNS = (
    "ты что-нибудь можешь мне посоветовать или предложить? я хочв расслабиться вечером и у меня ноет спина",
    "1. Спина, 2. После работы",
    "ну я назвал тебе конкретное место - спина",
    "и?",
)

PAIN_STEMS = ("спин", "боли", "ноет")


# --------------------------------------------------------------------------- #
# Обвязка канала                                                              #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _channel_harness(settings, monkeypatch):
    settings.GLOBAL_BOT_ONBOARDING = True
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


def _welcomed_user(user_id: int):
    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf1779",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _msg(*, text: str, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Андрей"},
            "recipient": {"chat_id": 8899, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


# --------------------------------------------------------------------------- #
# Честная заглушка модели                                                     #
# --------------------------------------------------------------------------- #
class _HonestModel:
    """Ведёт себя как живая модель 12.09: боль названа и инструмент
    предложен — вызывает ``health_screening``; иначе — проза.

    Запоминает, какие инструменты и какой system-prompt получала на каждом
    вызове, — по ним тест судит, что именно изменилось на ходу ответа.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete(self, messages, *, model, tools=None, **kwargs):
        offered = {t["name"] for t in (tools or [])}
        user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        system_text = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        self.calls.append({"tools": offered, "system": system_text, "user": user_text})
        pain = any(stem in user_text.lower() for stem in PAIN_STEMS)
        if pain and "health_screening" in offered:
            return CompletionResult(
                text="",
                tool_calls=[
                    ToolCall(
                        id="c1", name="health_screening", arguments={"symptom_text": user_text}
                    )
                ],
                prompt_tokens=30,
                completion_tokens=6,
                model="gpt-4o-mini",
                provider="openai",
                finish_reason="tool_calls",
            )
        return CompletionResult(
            text="Поняла: спина, после работы. Подойдёт массаж спины и шейно-воротниковой зоны — покажу мастеров в Пензе.",
            tool_calls=[],
            prompt_tokens=30,
            completion_tokens=20,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="stop",
        )


@pytest.fixture
def model(monkeypatch) -> _HonestModel:
    provider = _HonestModel()
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)
    return provider


_UID = iter(range(77901, 77999))


def _calls_for(model: _HonestModel, user_text: str) -> list[dict]:
    """Все вызовы модели на ходу с этой репликой (ход может стоить два
    вызова: DRF-1286 повторяет вызов с tool_choice=required, когда проза
    обещает действие без инструмента)."""
    found = [call for call in model.calls if call["user"] == user_text]
    assert found, [call["user"] for call in model.calls]
    return found


def _run_turns(sent, *turns: str):
    user_id = next(_UID)
    _bot_user, conversation = _welcomed_user(user_id)
    screens = []
    for index, text in enumerate(turns):
        max_handler.handle_global_max_event(_msg(text=text, user_id=user_id, mid=f"m{index}"))
        screens.append(sent[-1]["text"])
    conversation.refresh_from_db()
    return conversation, screens


# --------------------------------------------------------------------------- #
# Диалог владельца                                                            #
# --------------------------------------------------------------------------- #
class TestOwnerDialogue:
    def test_no_turn_gets_not_parsed(self, sent, model):
        conversation, screens = _run_turns(sent, *OWNER_TURNS)

        # Ход 1 — как и было: скрининг задаёт свои два вопроса.
        assert screens[0] == SOFT_PAIN_REPLY
        # Ходы 2–4 — ни одного «Не разобрала» (12.09 — было на ходу 2).
        assert templates.NOT_PARSED_RU not in screens[1:]
        assert all(screen != templates.NOT_PARSED_RU for screen in screens[1:])
        assert screens[1].startswith("Поняла: спина, после работы")

    def test_answer_turn_gets_the_question_as_a_fact_and_no_screening_tool(self, sent, model):
        _run_turns(sent, *OWNER_TURNS[:2])

        first = _calls_for(model, OWNER_TURNS[0])[0]
        second = _calls_for(model, OWNER_TURNS[1])[0]
        # Ход 1: инструмент предложен, блока ответа нет.
        assert "health_screening" in first["tools"]
        assert "Человек ответил" not in first["system"]
        # Ход 2: тот же инструмент НЕ предложен (памятка знает, что вопросы
        # заданы — суд вынесен до вызова модели), а вопрос и ответ — в prompt.
        # Присутствие раньше отсутствия: остальные инструменты на месте.
        assert "show_masters" in second["tools"]
        assert "health_screening" not in second["tools"]
        assert "Ты спросил: «Понимаю. Уточню, чтобы посоветовать точно:" in second["system"]
        assert "Человек ответил: «1. Спина, 2. После работы»" in second["system"]
        assert "Не задавай эти вопросы снова" in second["system"]

    def test_question_is_closed_by_the_answer_and_the_answer_is_kept(self, sent, model):
        conversation, _ = _run_turns(sent, *OWNER_TURNS[:2])

        assert open_question.pending_question(conversation) is None
        kept = conversation.skill_state[open_question.ANSWERED_KEY]
        assert kept["question_id"] == "health_screening.soft"
        assert kept["answer_text"] == "1. Спина, 2. После работы"

    def test_question_is_open_right_after_it_is_asked(self, sent, model):
        conversation, _ = _run_turns(sent, OWNER_TURNS[0])

        pending = open_question.pending_question(conversation)
        assert pending is not None
        assert pending.question_id == "health_screening.soft"
        assert pending.asked_text == SOFT_PAIN_REPLY

    def test_later_turns_do_not_reopen_the_question(self, sent, model):
        _run_turns(sent, *OWNER_TURNS)
        for text in OWNER_TURNS[2:]:
            for call in _calls_for(model, text):
                # Присутствие раньше отсутствия: инструменты и prompt на месте,
                # без скрининга и без блока ответа.
                assert "show_masters" in call["tools"]
                assert "health_screening" not in call["tools"]
                assert "Сегодня:" in call["system"]
                assert "Человек ответил" not in call["system"]


class TestRedFlagStillReachesScreening:
    def test_red_flag_in_the_answer_offers_the_tool(self, sent, model):
        """§35 п.5: тревожный признак включает безопасную ветку всегда —
        и на ходу ответа тоже. Памятка гасит только повтор SOFT."""
        answer = "спина, и ещё немеет рука и нога"
        _run_turns(sent, OWNER_TURNS[0], answer)
        assert "health_screening" in _calls_for(model, answer)[0]["tools"]


# --------------------------------------------------------------------------- #
# Состояние: срок и замещение                                                 #
# --------------------------------------------------------------------------- #
def _write_raw(conversation, value) -> None:
    """Сырая запись в состояние — в области тенанта разговора, как на пилоте."""
    from apps.conversations.services import write_skill_state
    from apps.tenancy.context import tenant_scope

    with tenant_scope(conversation.tenant):
        write_skill_state(conversation, open_question.STATE_KEY, value)


class TestState:
    def _conversation(self):
        _bot_user, conversation = _welcomed_user(next(_UID))
        return conversation

    def test_open_then_pending_then_close(self):
        conversation = self._conversation()
        open_question.open_question(conversation, "ask_clarification", asked_text="Какой город?")
        pending = open_question.pending_question(conversation)
        assert pending is not None and pending.question_id == "ask_clarification"

        answered = open_question.close_question(conversation, "Пенза")
        assert answered is not None
        assert answered.question.question_id == "ask_clarification"
        assert answered.answer_text == "Пенза"
        assert open_question.pending_question(conversation) is None
        # Второе закрытие — нечего закрывать.
        assert open_question.close_question(conversation, "и?") is None

    def test_expired_question_reads_as_not_asked(self):
        conversation = self._conversation()
        open_question.open_question(conversation, "health_screening.soft")
        assert open_question.pending_question(conversation) is not None

        stale = (
            timezone.now() - timedelta(seconds=open_question.STATE_TTL_SECONDS + 60)
        ).isoformat()
        _write_raw(
            conversation,
            {"question_id": "health_screening.soft", "asked_text": "", "at": stale},
        )
        assert open_question.pending_question(conversation) is None

    def test_second_question_replaces_the_first(self):
        conversation = self._conversation()
        open_question.open_question(conversation, "health_screening.soft")
        open_question.open_question(conversation, "ask_clarification", asked_text="Какой город?")
        pending = open_question.pending_question(conversation)
        assert pending is not None and pending.question_id == "ask_clarification"

    @pytest.mark.parametrize("garbage", [None, "строка", {"at": "вчера"}, {"question_id": ""}])
    def test_garbage_reads_as_not_asked(self, garbage):
        conversation = self._conversation()
        _write_raw(conversation, garbage)
        assert open_question.pending_question(conversation) is None

    def test_render_is_empty_without_an_answer(self):
        assert open_question.render_answer_block(None) == ""

    def test_render_names_question_answer_and_prohibitions(self):
        answered = open_question.AnsweredQuestion(
            question=open_question.OpenQuestion(
                question_id="ask_clarification", asked_text="Какой город?", asked_at=timezone.now()
            ),
            answer_text="Пенза",
        )
        block = open_question.render_answer_block(answered)
        assert "Ты спросил: «Какой город?»" in block
        assert "Человек ответил: «Пенза»" in block
        assert "Не обещай «проверить»" in block

    def test_never_raises_on_a_broken_conversation(self):
        broken = SimpleNamespace(skill_state="not-a-dict", id="x")
        assert open_question.pending_question(broken) is None
        open_question.open_question(broken, "ask_clarification")  # логирует, не бросает
        assert open_question.close_question(broken, "ответ") is None
